"""Durable paired de novo design through the production NanoCoFold adapters.

The treatment is design-stage hotspots. Follow-up predictors are unrestrained
in both arms. Deposited binder sequences and coordinates are never inputs.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import pandas as pd

from . import cli, design_routing, engine_comparison, provenance, screen, target_prep
from .config import load_config, dump_resolved, cfg_get
from .platform.jobs import allowed_gpu, gpu_lock_path, process_stamp
from .state import RunLock, RunState
from .utils.atomic import write_bytes
from .utils import seq, shell
from .utils.misc import to_native


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def save(path, value):
    write_bytes(Path(path), (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=str) + "\n").encode("utf-8"))


def validate_manifest(manifest):
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("At least one explicitly reviewed target is required")
    seen = set()
    for case in cases:
        pdb = case.get("pdb_id")
        if not isinstance(pdb, str) or not re.fullmatch(r"[A-Z0-9]{4}", pdb) or pdb in seen:
            raise ValueError("PDB target identifiers must be unique four-character IDs")
        seen.add(pdb)
        if "binder" in case:
            raise ValueError("Deposited binder records must not enter de novo design inputs")
        target = case.get("target", {})
        sequence = target.get("sequence")
        if not isinstance(sequence, str) or seq.clean_sequence(sequence) != sequence or not sequence:
            raise ValueError("Target requires an exact canonical amino-acid sequence")
        if any(c not in "ACDEFGHIKLMNPQRSTVWY" for c in sequence):
            raise ValueError("Target contains unsupported amino-acid symbols")
        import hashlib
        if hashlib.sha256(sequence.encode()).hexdigest() != target.get("sequence_sha256"):
            raise ValueError("Target sequence hash differs from reviewed manifest")
        positions = case.get("hotspots_seq_positions")
        if (not isinstance(positions, list) or not positions
                or any(type(p) is not int or p < 1 or p > len(sequence) for p in positions)
                or len(set(positions)) != len(positions)):
            raise ValueError("Hotspots require distinct exact 1-based target sequence positions")
    return cases


def case_config(cfg, case, arm, root):
    if arm not in {"off", "on"}:
        raise ValueError("Arm must be off or on")
    copied = copy.deepcopy(cfg)
    copied["run"].update(name=f'{case["pdb_id"]} · de novo VHH · hotspot {arm}', mode="design", resume=True)
    copied["target"].update(name=case["name"], sequence=case["target"]["sequence"], fasta=None, pdb=None,
        chain="A", aux_chains=[], hotspots=[f"seq{p}" for p in case["hotspots_seq_positions"]] if arm == "on" else [])
    copied["design"]["hotspot_mode"] = "specified" if arm == "on" else "none"
    copied["screen"]["restraint"]["enabled"] = False
    copied["screen"]["colabfold"].update(msa_dir=None, msa_manifest_sha256=None,
        msa_cache_dir=str((root / "monomer_cache").resolve()))
    copied.pop("input_review", None)
    return copied


def validate_protocol(cfg, allow_public_msa):
    if cfg_get(cfg, "run.dry_run", False):
        raise ValueError("This benchmark runs real or explicitly labelled mock computations, not dry-run")
    if cfg_get(cfg, "design.engine") != "both" or cfg_get(cfg, "design.num_designs") != 2:
        raise ValueError("Protocol requires RFantibody 2 and BoltzGen 2 per arm")
    if (cfg_get(cfg, "truncate.enabled", False) or cfg_get(cfg, "design.refinement.enabled", False)
            or cfg_get(cfg, "design.filters.enabled", True) or cfg_get(cfg, "specificity.enabled", False)):
        raise ValueError("Protocol excludes truncation, CDR refinement, design filtering and specificity campaigns")
    settings = engine_comparison.settings(cfg)
    if not settings["enabled"] or tuple(settings["engines"]) not in {
            engine_comparison.LEGACY_ENGINES, engine_comparison.ENGINES}:
        raise ValueError("Protocol requires the legacy three or all four independent prediction engines")
    if "opendde" in settings["engines"]:
        from .opendde import settings as opendde_settings
        options = opendde_settings(cfg)
        if options["samples"] != 1 or options["checkpoint"] != "opendde_abag.pt":
            raise ValueError("Protocol requires one OpenDDE antibody-antigen observation per candidate")
    if cfg_get(cfg, "screen.colabfold.msa_source") != "public_api":
        raise ValueError("This protocol requires explicitly authorized API MSA")
    if not cfg_get(cfg, "run.mock", False) and (not allow_public_msa or cfg_get(cfg, "screen.allow_public_msa_server") is not True):
        raise ValueError("Explicit --allow-public-msa and screen.allow_public_msa_server=true are required")
    if (cfg_get(cfg, "rank.require_hotspot", False) or cfg_get(cfg, "screen.restraint.enabled", True)
            or cfg_get(cfg, "screen.colabfold.num_models", 1) != 1 or cfg_get(cfg, "screen.colabfold.num_seeds", 1) != 1
            or cfg_get(cfg, "screen.num_diffn_samples", 1) != 1
            or cfg_get(cfg, "score.boltz2_diffusion_samples", 1) != 1
            or cfg_get(cfg, "design.mpnn.seqs_per_struct", 1) != 1):
        raise ValueError("Protocol requires unrestrained single-observation evaluation with identical gates")


def terminal_hashes(run):
    # Preserve and verify native coordinates, MSA, commands and completion
    # receipts as well as presentation tables. Lock/ledger bytes are mutable.
    return {str(path.relative_to(run)): provenance.file_digest(path) for path in sorted(run.rglob("*"))
            if path.is_file() and path.name not in {".run.lock", "case_receipt.json"}}


def prepared_target(cfg, run, root_receipt, mock):
    receipt_path = run / "prepared_target_receipt.json"
    identity = provenance.fingerprint(cfg)
    if receipt_path.exists():
        stored = json.loads(receipt_path.read_text())
        if (stored.get("config_identity") != identity
                or stored.get("artifacts") != provenance.output_manifest(run / "01_target")
                or (root_receipt and stored.get("raw_structure_sha256") != root_receipt["raw_structure_sha256"])):
            raise ValueError("Prepared arm target changed; preserve evidence before explicit recovery")
        target_prep._verified_raw_prediction(cfg, stored["prediction_receipt"], mock=mock)
        target = stored["target"]
        target_prep.validate_generated_target_identity(target["raw_structure"], {"A": target["sequence"]})
        return target
    # Normalize before the first design as well as before persistence. Native
    # QC can contain NaN; NaN -> null only on resume would change BG identity.
    target = to_native(target_prep.prepare_target(cfg, run, mock=mock, predicted_raw_receipt=root_receipt))
    save(receipt_path, {"config_identity": identity, "target": target,
        "prediction_receipt": target_prep.raw_prediction_receipt(cfg, target, mock=mock),
        "raw_structure_sha256": provenance.file_digest(target["raw_structure"]),
        "artifacts": provenance.output_manifest(run / "01_target")})
    return target


class ResourceUnavailable(RuntimeError):
    """A pre-submission resource guard blocked work; no terminal case failure."""


def resource_snapshot(root, gpu):
    disk_free = shutil.disk_usage(root).free
    if disk_free < 25 * 1024**3:
        raise ResourceUnavailable("Storage guard: less than 25 GiB free; existing outputs preserved")
    try:
        output = subprocess.check_output(["nvidia-smi", "--query-gpu=uuid,name,memory.free", "--format=csv,noheader,nounits"], text=True, timeout=15)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ResourceUnavailable("GPU resource probe failed before submission: " + str(exc)) from exc
    rows = [line.split(",") for line in output.splitlines()]
    match = next((r for r in rows if r[0].strip() == gpu), None)
    if not match:
        raise ResourceUnavailable("Approved GPU UUID is absent")
    available = None
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        available = next(int(line.split()[1]) * 1024 for line in meminfo.read_text().splitlines() if line.startswith("MemAvailable:"))
    return {"gpu": gpu, "gpu_name": match[1].strip(), "gpu_free_mib": int(match[2]),
            "disk_free_bytes": disk_free, "host_available_bytes": available, "checked_utc": utc()}


def open_gpu_lock(path):
    """Use the existing owned lease inode, including an owned deployment alias."""
    import stat
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() and path.lstat().st_uid != os.getuid():
        raise ValueError("Shared GPU reservation alias has invalid ownership")
    resolved = path.resolve()
    descriptor = os.open(resolved, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(descriptor)
    if (info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (path.stat().st_dev, path.stat().st_ino)):
        os.close(descriptor)
        raise ValueError("Shared GPU reservation has invalid ownership/type/inode")
    return os.fdopen(descriptor, "a+")


def run_benchmark(root, manifest_path, config_path, *, gpu=None, allow_public_msa=False, budget_seconds=43200):
    if isinstance(budget_seconds, bool) or not math.isfinite(budget_seconds) or budget_seconds <= 0:
        raise ValueError("Campaign budget must be a positive finite number of seconds")
    root, manifest_path, config_path = map(lambda p: Path(p).resolve(), (root, manifest_path, config_path))
    manifest = json.loads(manifest_path.read_text())
    cases = validate_manifest(manifest)
    cfg = load_config(config_path)
    validate_protocol(cfg, allow_public_msa)
    engines = engine_comparison.settings(cfg)["engines"]
    declared = manifest.get("protocol", {}).get("followup_engines")
    if declared is not None and declared != engines:
        raise ValueError("Manifest follow-up engines differ from the execution config; no inference submitted")
    expected_models = len(cases) * 8 * len(engines)
    mock = cfg_get(cfg, "run.mock", False) is True
    if not mock:
        if not gpu or not str(gpu).startswith("GPU-"):
            raise ValueError("Live execution requires an explicitly permitted GPU UUID")
        gpu = allowed_gpu(gpu)
        if os.environ.get("CUDA_VISIBLE_DEVICES") != gpu:
            raise ValueError("CUDA_VISIBLE_DEVICES must equal the one approved GPU UUID")
    root.mkdir(parents=True, exist_ok=True)
    sources = {str(p.relative_to(Path(__file__).parent)): provenance.file_digest(p)
               for p in sorted(Path(__file__).parent.rglob("*.py"))}
    identity = {"schema_version": 1, "input_sha256": provenance.file_digest(manifest_path),
                "config_sha256": provenance.file_digest(config_path), "source_sha256": sources,
                "gpu": gpu, "mock": mock, "public_msa_authorized": allow_public_msa,
                "budget_seconds": budget_seconds,
                "runtime": {e: provenance.runtime_identity(cfg, e) for e in
                            ("rfantibody", "boltzgen", *engines)} if not mock else {}}
    identity_path = root / "execution_identity.json"
    with RunLock(root):
        if identity_path.exists():
            previous = json.loads(identity_path.read_text())
            # Time is an operational limit, not a scientific input. Preserve
            # strict source/runtime/input identity while permitting more time.
            if {k: v for k, v in previous.items() if k != "budget_seconds"} != {k: v for k, v in identity.items() if k != "budget_seconds"}:
                raise ValueError("Campaign identity changed; preserve this campaign before explicit recovery")
        reviewed_input = root / "inputs_manifest.json"
        if reviewed_input.exists():
            if provenance.file_digest(reviewed_input) != identity["input_sha256"]:
                raise ValueError("Preserved campaign input differs from reviewed manifest")
        elif manifest_path != reviewed_input:
            temporary = reviewed_input.with_suffix(".json.partial")
            temporary.write_bytes(manifest_path.read_bytes())
            if provenance.file_digest(temporary) != identity["input_sha256"]:
                raise ValueError("Reviewed input changed during campaign preparation")
            temporary.replace(reviewed_input)
        if not identity_path.exists():
            save(identity_path, identity)
        status_path = root / "benchmark_status.json"
        status = json.loads(status_path.read_text()) if status_path.is_file() else {
            "schema_version": 1, "status": "starting", "started_utc": utc(), "cases": [], "completed_cases": 0, "generated_candidates": 0, "prediction_rows": 0,
            "expected_cases": len(cases) * 2, "expected_candidates": len(cases) * 8,
            "expected_models": expected_models, "mock": mock, "elapsed_seconds": 0}
        expected_keys = {(case["pdb_id"], arm) for case in cases for arm in ("off", "on")}
        case_keys = [(entry.get("pdb_id"), entry.get("arm")) for entry in status.get("cases", [])]
        if (status.get("schema_version") != 1 or not isinstance(status.get("cases"), list)
                or len(set(case_keys)) != len(case_keys) or not set(case_keys) <= expected_keys
                or any(entry.get("status") not in {"complete", "partial_failure", "failed"} for entry in status["cases"])
                or status.get("expected_cases") != len(cases) * 2 or status.get("expected_candidates") != len(cases) * 8
                or status.get("expected_models") != expected_models
                or (status.get("status") in {"complete", "partial_failure"} and set(case_keys) != expected_keys)):
            raise ValueError("Campaign ledger identity/coverage is invalid; no inference submitted")
        for entry in status["cases"]:
            target_receipt = root / "targets" / (entry["pdb_id"] + ".json")
            if (terminal_hashes(root / entry["run_dir"]) != entry["artifact_sha256"]
                    or (entry.get("target_receipt_sha256") and (
                        not target_receipt.is_file() or provenance.file_digest(target_receipt) != entry["target_receipt_sha256"]))):
                raise ValueError("Completed case evidence changed; no inference submitted")
        if status["status"] in {"complete", "partial_failure"}:
            return status
        first_elapsed = status["elapsed_seconds"]
        began = time.monotonic()
        lock = None
        def checkpoint(**values):
            status.update(values, elapsed_seconds=first_elapsed + time.monotonic() - began, updated_utc=utc())
            save(status_path, status)
        if budget_seconds <= first_elapsed:
            raise TimeoutError("Campaign budget already consumed. Resume with a larger --budget-seconds; scientific identity remains fixed.")
        save(root / "budget_requests" / (str(time.time_ns()) + ".json"),
             {"budget_seconds": budget_seconds, "elapsed_seconds": first_elapsed, "requested_utc": utc()})
        if status.get("error"):
            save(root / "interruption_history" / (str(time.time_ns()) + ".json"),
                 {"error": status.pop("error"), "resume_utc": utc(), "prior_status": status["status"]})
        interruption = root / "interruption_receipt.json"
        if interruption.is_file():
            history = root / "interruption_history" / (str(time.time_ns()) + "-receipt.json")
            history.parent.mkdir(parents=True, exist_ok=True)
            interruption.replace(history)
        checkpoint(status="resuming")
        def resources():
            if mock:
                return
            started = time.monotonic()
            while True:
                shell.remaining_budget()
                if (root / "cancel.request").exists():
                    raise shell.ExecutionCancelled("Explicit campaign cancellation requested")
                snapshot = resource_snapshot(root, gpu)
                save(root / "resource_latest.json", snapshot)
                if snapshot["gpu_free_mib"] >= 32768 and (snapshot["host_available_bytes"] is None or snapshot["host_available_bytes"] >= 32 * 1024**3):
                    return
                checkpoint(status="resource_wait")
                if time.monotonic() - started >= 7200:
                    raise TimeoutError("Two-hour resource wait expired; no duplicate submission")
                time.sleep(10)
        def predict(*args, **kwargs):
            resources()
            checkpoint(status="running", current_step="prediction", current_engine=kwargs.get("engine"),
                       current_candidate=str(args[3].iloc[0]["design_id"]))
            return screen.predict_complexes(*args, **kwargs)
        try:
            with shell.execution_budget(budget_seconds - first_elapsed), shell.cancellation_scope(lambda: (root / "cancel.request").exists()):
                if not mock:
                    path = gpu_lock_path(gpu)
                    lock = open_gpu_lock(path)
                    descriptor = lock.fileno()
                    while True:
                        shell.remaining_budget()
                        if (root / "cancel.request").exists():
                            raise shell.ExecutionCancelled("Explicit cancellation while waiting for GPU lease")
                        try:
                            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            checkpoint(status="gpu_lock_wait")
                            time.sleep(10)
                    os.environ["NANOCOFOLD_GPU_LOCK_FD"] = str(descriptor)
                save(root / "worker_identity.json", {"pid": os.getpid(), "birthstamp": process_stamp(os.getpid()),
                    "gpu": gpu, "started_utc": utc(), "mock": mock,
                    "execution_identity_sha256": provenance.file_digest(identity_path),
                    "lock_inode": os.fstat(lock.fileno()).st_ino if lock else None})
                for case in cases:
                    pdb = case["pdb_id"]
                    for arm in ("off", "on"):
                        if any(e["pdb_id"] == pdb and e["arm"] == arm for e in status["cases"]):
                            continue
                        run = root / "cases" / pdb / arm
                        run.mkdir(parents=True, exist_ok=True)
                        applied = case_config(cfg, case, arm, root)
                        dump_resolved(applied, run / "run_config.yaml")
                        state, timings = RunState(run), {}
                        record = {"pdb_id": pdb, "arm": arm, "run_dir": str(run.relative_to(root)),
                                  "status": "running", "started_utc": utc(), "generated_candidates": 0,
                                  "prediction_rows": 0, "timings": timings}
                        begin = time.monotonic()
                        checkpoint(status="running", current_pdb=pdb, current_arm=arm, current_step="target", current_engine=None, current_candidate=None)
                        print(f"START {pdb} {arm}", flush=True)
                        try:
                            with RunLock(run):
                                resources()
                                state.begin_phase("target")
                                receipt_path = root / "targets" / (pdb + ".json")
                                receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else None
                                t = time.monotonic()
                                target = prepared_target(applied, run, receipt, mock)
                                timings["target_preparation_seconds"] = time.monotonic() - t
                                state.set_phase("target", target)
                                if not receipt:
                                    receipt = target_prep.raw_prediction_receipt(applied, target, mock=mock)
                                    save(receipt_path, receipt)
                                record["target_raw_sha256"] = receipt["raw_structure_sha256"]
                                checkpoint(current_step="design")
                                state.begin_phase("design")
                                resources()
                                t = time.monotonic()
                                generated = design_routing.run_design(applied, run, target, mock=mock)
                                timings["design_seconds"] = time.monotonic() - t
                                state.set_phase("design", generated["info"])
                                record["generated_candidates"] = len(generated["candidates"])
                                record["generated_records"] = len(generated["all_candidates"])
                                for engine, info in generated["info"]["engines"].items():
                                    timings[engine + "_design_seconds"] = info["adapter_elapsed_seconds"]
                                if generated["candidates"].empty:
                                    raise ValueError("No generated candidate sequences available for independent evaluation")
                                checkpoint(current_step="msa_and_evaluation")
                                t = time.monotonic()
                                engine_comparison.run(applied, run, state, target, generated["candidates"],
                                                      predict=predict, finalize=cli._finalize)
                                timings["msa_prediction_scoring_report_seconds"] = time.monotonic() - t
                                comparison = json.loads((run / "02_comparison/comparison_status.json").read_text())
                                record.update(status=comparison["status"], prediction_rows=len(comparison["rows"]),
                                    prediction_completed=sum(r["status"] == "complete" for r in comparison["rows"]),
                                    prediction_failed=sum(r["status"] == "failed" for r in comparison["rows"]))
                                scored = pd.read_csv(run / "03_score/scored_candidates.csv")
                                evaluated = scored.get("final_evaluated", pd.Series(False, index=scored.index)).eq(True)
                                errors = scored.get("error", pd.Series(None, index=scored.index, dtype=object)).notna()
                                record["models_evaluated"] = int((evaluated & ~errors).sum())
                                predicted_complete = scored.get("prediction_status", pd.Series("complete", index=scored.index)).eq("complete")
                                record["scoring_failed"] = int(((~evaluated | errors) & predicted_complete).sum())
                                if len(scored) != record["prediction_rows"] or (~evaluated | errors).any():
                                    record["status"] = "partial_failure"
                        except (shell.ExecutionCancelled, KeyboardInterrupt, TimeoutError, subprocess.TimeoutExpired, ResourceUnavailable):
                            raise
                        except Exception as exc:
                            record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                            # A later generator can fail after earlier native
                            # sequences were saved. Use the report's lossless
                            # collector so live counts and delivery agree.
                            from .design_benchmark_report import _candidate_records
                            try:
                                preserved = _candidate_records(run, pdb + "_" + arm)
                                record["generated_records"] = len(preserved)
                                record["generated_candidates"] = sum(row["generation_complete"] for row in preserved)
                                engine_info = run / "02_design/engine_info.json"
                                if engine_info.is_file():
                                    for engine, info in json.loads(engine_info.read_text()).items():
                                        if isinstance(info.get("adapter_elapsed_seconds"), (int, float)):
                                            timings[engine + "_design_seconds"] = info["adapter_elapsed_seconds"]
                            except (OSError, ValueError, TypeError, KeyError) as evidence_error:
                                record["count_reconciliation_error"] = str(evidence_error)
                            save(run / "failure_receipt.json", {**record, "failed_utc": utc()})
                            print(f"FAILED {pdb} {arm}: {record['error']}", flush=True)
                        record.update(elapsed_seconds=time.monotonic() - begin, completed_utc=utc(),
                                      artifact_sha256=terminal_hashes(run))
                        if (root / "targets" / (pdb + ".json")).is_file():
                            record["target_receipt_sha256"] = provenance.file_digest(root / "targets" / (pdb + ".json"))
                        save(run / "case_receipt.json", record)
                        status["cases"].append(record)
                        checkpoint(completed_cases=len(status["cases"]),
                            generated_candidates=sum(r["generated_candidates"] for r in status["cases"]),
                            prediction_rows=sum(r["prediction_rows"] for r in status["cases"]))
                        print(f"END {pdb} {arm} {record['status']} {record['elapsed_seconds']:.1f}s", flush=True)
                checkpoint(status="complete" if all(c["status"] == "complete" for c in status["cases"]) else "partial_failure",
                           completed_utc=utc(), current_pdb=None, current_arm=None, current_step=None,
                           current_engine=None, current_candidate=None)
        except BaseException as exc:
            checkpoint(status="interrupted", error=f"{type(exc).__name__}: {exc}")
            save(root / "interruption_receipt.json", {"error": status["error"], "utc": utc()})
            raise
        finally:
            os.environ.pop("NANOCOFOLD_GPU_LOCK_FD", None)
            if lock:
                lock.close()
        return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--gpu")
    parser.add_argument("--allow-public-msa", action="store_true")
    parser.add_argument("--budget-seconds", type=float, default=43200)
    parser.add_argument("--report-output", type=Path)
    parser.add_argument("--reference-manifest", type=Path,
                        help="Post-inference report reference only; never a predictor input")
    args = parser.parse_args()
    result = run_benchmark(args.root, args.inputs, args.config, gpu=args.gpu,
                           allow_public_msa=args.allow_public_msa, budget_seconds=args.budget_seconds)
    if args.report_output:
        from .design_benchmark_report import build_design_benchmark
        build_design_benchmark(args.root, args.report_output, reference_manifest=args.reference_manifest)
    print(json.dumps({k: result.get(k) for k in ("status", "completed_cases", "generated_candidates", "prediction_rows", "elapsed_seconds")}), flush=True)


if __name__ == "__main__":
    main()
