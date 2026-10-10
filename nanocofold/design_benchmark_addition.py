"""One-shot OpenDDE evaluation of an immutable, completed legacy campaign.

Generated sequences, targets and monomer alignments are reused. No design,
target preparation or MSA acquisition is invoked. A nonterminal launch intent
requires manual reconciliation, even when its process has exited.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import math
import os
import subprocess
from pathlib import Path
import time

import pandas as pd
import yaml

from . import cli, design_benchmark as legacy, engine_comparison, opendde, provenance, screen
from .colabfold_msa import cached_monomer
from .config import cfg_get, cfg_seed, dump_resolved
from .platform.jobs import allowed_gpu, gpu_lock_path, process_stamp
from .state import RunLock, RunState
from .utils import seq, shell
from .utils.atomic import write_json

COUNTS = {"expected_cases": 20, "expected_candidates": 80, "expected_models": 80}
TERMINAL = {"complete", "partial_failure", "failed"}


class SourceChanged(shell.ExecutionCancelled):
    """Stop submission when immutable source evidence changes."""


def _json(path):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _save(path, value):
    write_json(Path(path), value)


def _contained(root, value):
    root = Path(root).resolve()
    path = Path(value)
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"Required evidence must be a file within its source: {value}")
    return path


def _tree(root, *, exclude=()):
    root = Path(root).resolve()
    hashes = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and str(path.relative_to(root)) not in exclude:
            shell.remaining_budget()
            hashes[str(path.relative_to(root))] = provenance.file_digest(_contained(root, path))
    shell.remaining_budget()
    return hashes


def _assert_source(source, hashes):
    if _tree(source) != hashes:
        raise SourceChanged("Legacy source changed; no further inference permitted")


def _msas(source, cfg, target, candidates, expected):
    cache = Path(cfg_get(cfg, "screen.colabfold.msa_cache_dir", "")).resolve()
    if not cache.is_dir() or not cache.is_relative_to(source):
        raise ValueError("Original monomer cache must be contained in the legacy campaign")
    chains, _, binder = screen.complex_chain_records(target, "")
    queries = {"chain::" + letter: sequence for letter, sequence in chains.items() if letter != binder}
    queries.update({"candidate::" + row["design_id"]: row["sequence"] for row in candidates.to_dict("records")})
    if len(queries) != 5 or set(expected) != set(queries):
        raise ValueError("Legacy case requires exactly five recorded monomer MSA identities")
    for key, sequence in queries.items():
        entry = cached_monomer(cache, sequence)
        _contained(source, entry["a3m"])
        if entry["sha256"] != expected[key]:
            raise ValueError("Original comparison MSA hash differs from frozen cache: " + key)
    return cache


def validate_source(source, *, mock=False):
    source = Path(source).resolve()
    inputs = _json(_contained(source, "inputs_manifest.json"))
    targets = {case["pdb_id"]: case for case in legacy.validate_manifest(inputs)}
    status = _json(_contained(source, "benchmark_status.json"))
    execution = _json(_contained(source, "execution_identity.json"))
    records = status.get("cases", [])
    keys = {(pdb, arm) for pdb in targets for arm in ("off", "on")}
    if (len(targets) != 10 or status.get("schema_version") != 1 or status.get("status") != "complete"
            or status.get("mock") is not mock or execution.get("mock") is not mock
            or status.get("expected_cases") != 20 or status.get("expected_candidates") != 80
            or status.get("expected_models") != 240 or status.get("generated_candidates") != 80
            or status.get("prediction_rows") != 240 or len(records) != 20
            or {(r.get("pdb_id"), r.get("arm")) for r in records} != keys
            or execution.get("input_sha256") != provenance.file_digest(source / "inputs_manifest.json")
            or inputs.get("protocol", {}).get("followup_engines", list(engine_comparison.LEGACY_ENGINES)) != list(engine_comparison.LEGACY_ENGINES)):
        raise ValueError("Source must be the exact completed legacy 20-case/80-candidate/240-model campaign")
    cases = []
    for record in records:
        pdb, arm = record["pdb_id"], record["arm"]
        run = source / "cases" / pdb / arm
        if (record.get("run_dir") != str(run.relative_to(source)) or record.get("status") != "complete"
                or record.get("generated_candidates") != 4 or record.get("prediction_rows") != 12
                or record.get("prediction_completed") != 12 or record.get("models_evaluated") != 12):
            raise ValueError("Source case path or completion identity is invalid")
        if legacy.terminal_hashes(run) != record.get("artifact_sha256") or _json(_contained(run, "case_receipt.json")) != record:
            raise ValueError("Source case sealed artifact hashes changed")
        required = ("run_config.yaml", "prepared_target_receipt.json", "02_design/all_generated_candidates.csv",
                    "02_design/engine_info.json", "02_comparison/comparison_identity.json",
                    "02_comparison/comparison_status.json", "03_score/scored_candidates.csv")
        for name in required:
            _contained(run, name)
            if name not in record["artifact_sha256"]:
                raise ValueError("Source case lacks sealed canonical evidence: " + name)
        cfg = yaml.safe_load((run / "run_config.yaml").read_text())
        if not isinstance(cfg, dict) or cfg_get(cfg, "run.mock", False) is not mock:
            raise ValueError("Historical case config execution mode differs")
        cfg_seed(cfg, "run.seed")
        target_receipt = _contained(source, "targets/" + pdb + ".json")
        if record.get("target_receipt_sha256") != provenance.file_digest(target_receipt):
            raise ValueError("Source target receipt hash changed")
        prepared, root_target = _json(run / "prepared_target_receipt.json"), _json(target_receipt)
        target = prepared["target"]
        raw = _contained(source, target["raw_structure"])
        if (target["sequence"] != targets[pdb]["target"]["sequence"]
                or cfg_get(cfg, "target.sequence") != target["sequence"]
                or prepared.get("artifacts") != provenance.output_manifest(run / "01_target")
                or provenance.file_digest(raw) != prepared.get("raw_structure_sha256")
                or prepared["raw_structure_sha256"] != root_target.get("raw_structure_sha256")
                or record.get("target_raw_sha256") != prepared["raw_structure_sha256"]):
            raise ValueError("Source prepared target and reviewed sequence/receipt differ")
        frame = pd.read_csv(run / "02_design/all_generated_candidates.csv")
        if (len(frame) != 4 or not {"design_id", "sequence", "parent_engine"} <= set(frame)
                or frame["design_id"].duplicated().any()
                or frame["parent_engine"].value_counts().to_dict() != {"rfantibody": 2, "boltzgen": 2}
                or any(not isinstance(s, str) or not s or seq.clean_sequence(s) != s
                       or set(s) - set("ACDEFGHIKLMNPQRSTVWY") for s in frame["sequence"])
                or frame.get("generation_error", pd.Series(dtype=object)).notna().any()
                or frame.get("generation_status", pd.Series(dtype=object)).isin(["failed", "interrupted"]).any()):
            raise ValueError("Source requires two RFantibody and two BoltzGen canonical candidates")
        for identifier in frame["design_id"]:
            seq.validate_candidate_id(identifier)
        if set(_json(run / "02_design/engine_info.json")) != {"rfantibody", "boltzgen"}:
            raise ValueError("Source generator identity is invalid")
        comparison = _json(run / "02_comparison/comparison_identity.json")
        payload = {key: value for key, value in comparison.items() if key != "fingerprint"}
        candidate_rows = [{"id": row["design_id"], "sequence": row["sequence"]} for row in frame.to_dict("records")]
        if (comparison.get("fingerprint") != provenance.fingerprint(payload)
                or comparison.get("engines") != list(engine_comparison.LEGACY_ENGINES)
                or comparison.get("mock") is not mock or comparison.get("candidates") != candidate_rows
                or comparison.get("target") != {key: target.get(key) for key in ("sequence", "aux_chains", "hotspot_seq_positions")}):
            raise ValueError("Source comparison identity differs from canonical candidates/target")
        expected_rows = {(engine + "__" + row["id"], engine) for engine in engine_comparison.LEGACY_ENGINES for row in candidate_rows}
        observed = _json(run / "02_comparison/comparison_status.json")
        rows = observed.get("rows", [])
        scored = pd.read_csv(run / "03_score/scored_candidates.csv")
        if (observed.get("status") != "complete" or observed.get("expected_rows") != 12
                or len(rows) != 12 or any(row.get("status") != "complete" for row in rows)
                or {(r.get("id"), r.get("engine")) for r in rows} != expected_rows
                or len(scored) != 12 or not {"design_id", "predictor_engine", "sequence", "final_evaluated"} <= set(scored)
                or set(zip(scored["design_id"], scored["predictor_engine"])) != expected_rows
                or not scored["final_evaluated"].eq(True).all()
                or scored.get("error", pd.Series(dtype=object)).notna().any()):
            raise ValueError("Source requires twelve exact complete legacy scored observations")
        sequence_by_id = {engine + "__" + row["id"]: row["sequence"] for engine in engine_comparison.LEGACY_ENGINES for row in candidate_rows}
        if any(row["sequence"] != sequence_by_id[row["design_id"]] for row in scored.to_dict("records")):
            raise ValueError("Source scored candidate sequence identity differs")
        if not mock:
            if "cif_path" not in scored or scored["cif_path"].isna().any():
                raise ValueError("Live source requires all twelve sealed native coordinate artifacts")
            for value in scored["cif_path"]:
                path = _contained(run, run / value)
                if path.stat().st_size == 0 or str(path.relative_to(run)) not in record["artifact_sha256"]:
                    raise ValueError("Live source native coordinate artifact is empty or unsealed")
        cache = _msas(source, cfg, target, frame, comparison.get("msa_hashes", {}))
        cases.append({"record": record, "run": run, "cfg": cfg, "target": target, "candidates": frame,
                      "cache": cache, "msa_hashes": comparison["msa_hashes"], "required": required})
    return inputs, cases, _tree(source)


def _options(config_path):
    explicit = yaml.safe_load(Path(config_path).read_text())
    if not isinstance(explicit, dict):
        raise ValueError("Explicit addition config must be a YAML object")
    mock = cfg_get(explicit, "run.mock", False)
    if type(mock) is not bool:
        raise ValueError("Addition run.mock must be true/false")
    options = opendde.settings(explicit)
    if options["samples"] != 1:
        raise ValueError("Addition requires one OpenDDE observation per original candidate")
    for key in ("env", "data_dir"):
        path = Path(options[key])
        if not mock and not path.is_absolute():
            raise ValueError("Live OpenDDE env/data_dir must be explicit absolute paths")
        options[key] = str((Path(config_path).resolve().parent / path).resolve())
    return {"enabled": True, **options}, mock


def _case_config(case, options, mock):
    cfg = copy.deepcopy(case["cfg"])
    cfg["run"].update(mock=mock, dry_run=False, resume=False)
    cfg["screen"]["comparison"] = {"enabled": True, "engines": ["opendde"], "continue_on_error": True}
    cfg["screen"]["opendde"] = copy.deepcopy(options)
    cfg["screen"]["allow_public_msa_server"] = False
    cfg["screen"]["restraint"]["enabled"] = False
    cfg["screen"]["colabfold"].update(msa_source="cache", msa_cache_dir=str(case["cache"]), msa_dir=None, msa_manifest_sha256=None)
    return cfg


def _counts(status):
    cases = status["cases"]
    observations = [row for case in cases for row in case["observations"]]
    return {"completed_cases": sum(c["status"] in TERMINAL for c in cases), "generated_candidates": 80,
            "prediction_rows": sum(o["status"] in {"complete", "failed"} for o in observations),
            "prediction_completed": sum(o["status"] == "complete" for o in observations),
            "prediction_failed": sum(o["status"] == "failed" for o in observations),
            "prediction_unstarted": sum(o["status"] == "unstarted" for o in observations),
            "prediction_running": sum(o["status"] == "running" for o in observations),
            "prediction_interrupted": sum(o["status"] == "interrupted" for o in observations),
            "models_evaluated": sum(c.get("models_evaluated", 0) for c in cases)}


def _resources_ready(snapshot):
    # Unknown host memory cannot establish the explicitly requested RAM gate.
    thresholds = {"gpu_free_mib": 32768, "host_available_bytes": 32 * 1024**3,
                  "disk_free_bytes": 25 * 1024**3}
    return all(type(snapshot.get(key)) in (int, float) and math.isfinite(snapshot[key])
               and snapshot[key] >= minimum for key, minimum in thresholds.items())


def _terminal(root, status, identity):
    receipt = _json(root / "addition_complete.json")
    keys = {(r["pdb_id"], r["arm"]) for r in identity["source_cases"]}
    if (status.get("schema_version") != 1 or status.get("status") not in {"complete", "partial_failure"}
            or any(status.get(k) != v for k, v in COUNTS.items()) or len(status.get("cases", [])) != 20
            or {(r.get("pdb_id"), r.get("arm")) for r in status["cases"]} != keys
            or any(r.get("status") not in TERMINAL or len(r.get("observations", [])) != 4 for r in status["cases"])
            or any(status.get(k) != v for k, v in _counts(status).items())
            or receipt.get("schema_version") != 1 or receipt.get("status") != status["status"]
            or receipt.get("execution_fingerprint") != provenance.fingerprint(identity)
            or receipt.get("artifacts") != _tree(root, exclude=(".run.lock", "addition_complete.json"))):
        raise ValueError("Terminal addition evidence changed; no inference permitted")
    return status


def run_addition(source, root, config_path, *, gpu=None, budget_seconds=43200):
    if (isinstance(budget_seconds, bool) or not isinstance(budget_seconds, (int, float))
            or not math.isfinite(budget_seconds) or not 0 < budget_seconds <= 43200):
        raise ValueError("Addition budget must be positive and at most twelve hours")
    started = time.monotonic()
    started_utc = legacy.utc()
    source, root, config_path = Path(source).resolve(), Path(root).resolve(), Path(config_path).resolve()
    if root.is_relative_to(source) or source.is_relative_to(root):
        raise ValueError("Addition root and immutable source must be separate")
    options, mock = _options(config_path)
    if not mock:
        if not isinstance(gpu, str) or not gpu.startswith("GPU-") or allowed_gpu(gpu) != gpu:
            raise ValueError("Live addition requires an explicitly permitted GPU UUID")
        if os.environ.get("CUDA_VISIBLE_DEVICES") != gpu:
            raise ValueError("CUDA_VISIBLE_DEVICES must equal the one approved GPU UUID")
    inputs, cases, source_hashes = validate_source(source, mock=mock)
    package = Path(__file__).parent
    identity = {"schema_version": 1, "source": str(source), "source_artifacts": source_hashes,
                "source_cases": [{"pdb_id": c["record"]["pdb_id"], "arm": c["record"]["arm"],
                                  "case_receipt_sha256": provenance.file_digest(c["run"] / "case_receipt.json")} for c in cases],
                "config_sha256": provenance.file_digest(config_path), "options": options, "mock": mock,
                "gpu": gpu, "budget_seconds": budget_seconds,
                "code_sha256": {str(p.relative_to(package)): provenance.file_digest(p) for p in sorted(package.rglob("*.py"))},
                "runtime": provenance.runtime_identity({"screen": {"opendde": options}}, "opendde") if not mock else {}}
    with RunLock(root):
        intent = root / "launch_intent.json"
        if intent.exists():
            if (_json(intent).get("execution_fingerprint") != provenance.fingerprint(identity)
                    or not (root / "execution_identity.json").is_file() or _json(root / "execution_identity.json") != identity
                    or not (root / "addition_complete.json").is_file()):
                raise ValueError("Existing nonterminal/changed addition: manual reconciliation required; automatic resume prohibited")
            return _terminal(root, _json(root / "benchmark_status.json"), identity)
        if any(p.name != ".run.lock" for p in root.iterdir()):
            raise ValueError("Addition root contains uncertain evidence; use a fresh root")
        _save(intent, {"schema_version": 1, "created_utc": legacy.utc(), "automatic_retry_allowed": False,
                       "execution_fingerprint": provenance.fingerprint(identity)})
        _save(root / "execution_identity.json", identity)
        _save(root / "augmentation_identity.json", {"schema_version": 1, "source": str(source),
              "source_artifacts": source_hashes, "followup_engines": ["opendde"], "generated_sequences_reused": 80})
        copied_inputs = copy.deepcopy(inputs)
        copied_inputs.setdefault("protocol", {})["followup_engines"] = ["opendde"]
        _save(root / "inputs_manifest.json", copied_inputs)
        # Both clocks start before source validation and runtime preparation.
        # Elapsed includes setup, resource/GPU waits, prediction and scoring through
        # the final status checkpoint; subsequent artifact sealing/return is excluded.
        status = {"schema_version": 1, "status": "planned", "started_utc": started_utc, "mock": mock,
                  "elapsed_scope": "runner_entry_through_final_status_checkpoint",
                  "timestamp_scope": "runner_entry_and_final_status_checkpoint",
                  **COUNTS, "cases": [], "elapsed_seconds": 0}
        for case in cases:
            record = case["record"]
            run = root / record["run_dir"]
            for relative in case["required"] + ("case_receipt.json",):
                destination = run / "source_evidence" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((case["run"] / relative).read_bytes())
            for relative in ("02_design/all_generated_candidates.csv", "02_design/engine_info.json"):
                destination = run / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((case["run"] / relative).read_bytes())
            destination = root / "targets" / (record["pdb_id"] + ".json")
            destination.parent.mkdir(exist_ok=True)
            destination.write_bytes((source / "targets" / destination.name).read_bytes())
            dump_resolved(_case_config(case, options, mock), run / "run_config.yaml")
            status["cases"].append({"pdb_id": record["pdb_id"], "arm": record["arm"], "run_dir": record["run_dir"],
                "status": "unstarted", "generated_candidates": 4, "generated_records": 4,
                "source_case_receipt_sha256": provenance.file_digest(case["run"] / "case_receipt.json"),
                "target_raw_sha256": record["target_raw_sha256"], "timings": {},
                "observations": [{"id": "opendde__" + row["design_id"], "source_sequence_id": row["design_id"],
                                  "engine": "opendde", "status": "unstarted"} for row in case["candidates"].to_dict("records")]})
        def checkpoint(**values):
            status.update(values, **_counts(status), elapsed_seconds=time.monotonic() - started, updated_utc=legacy.utc())
            _save(root / "benchmark_status.json", status)
        checkpoint()
        lock, previous_fd = None, os.environ.get("NANOCOFOLD_GPU_LOCK_FD")
        def resources():
            if mock:
                shell.remaining_budget()
                return
            began = time.monotonic()
            while True:
                shell.remaining_budget()
                if (root / "cancel.request").exists():
                    raise shell.ExecutionCancelled("Explicit addition cancellation requested")
                snapshot = legacy.resource_snapshot(root, gpu)
                _save(root / "resource_latest.json", snapshot)
                if _resources_ready(snapshot):
                    return
                checkpoint(status="resource_wait")
                if time.monotonic() - began >= 7200:
                    raise TimeoutError("Two-hour resource wait expired; automatic retry prohibited")
                time.sleep(10)
        try:
            with shell.execution_budget(budget_seconds - (time.monotonic() - started)), shell.cancellation_scope(lambda: (root / "cancel.request").exists()):
                shell.remaining_budget()
                if not mock:
                    lock = legacy.open_gpu_lock(gpu_lock_path(gpu))
                    while True:
                        shell.remaining_budget()
                        if (root / "cancel.request").exists():
                            raise shell.ExecutionCancelled("Cancellation while waiting for GPU lease")
                        try:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            checkpoint(status="gpu_lock_wait")
                            time.sleep(10)
                    os.environ["NANOCOFOLD_GPU_LOCK_FD"] = str(lock.fileno())
                _save(root / "worker_identity.json", {"pid": os.getpid(), "birthstamp": process_stamp(os.getpid()),
                      "gpu": gpu, "mock": mock, "lock_inode": os.fstat(lock.fileno()).st_ino if lock else None,
                      "execution_identity_sha256": provenance.file_digest(root / "execution_identity.json")})
                for case, record in zip(cases, status["cases"]):
                    _assert_source(source, source_hashes)
                    run = root / record["run_dir"]
                    cfg = _case_config(case, options, mock)
                    record.update(status="running", started_utc=legacy.utc())
                    checkpoint(status="running", current_pdb=record["pdb_id"], current_arm=record["arm"])
                    def predict(*args, **kwargs):
                        _assert_source(source, source_hashes)
                        resources()
                        identifier = str(args[3].iloc[0]["design_id"])
                        observation = next(r for r in record["observations"] if r["id"] == identifier)
                        observation.update(status="running", started_utc=legacy.utc())
                        checkpoint(status="running", current_engine="opendde", current_candidate=identifier)
                        try:
                            result = screen.predict_complexes(*args, **kwargs)
                            if len(result) != 1 or str(result.iloc[0]["design_id"]) != identifier:
                                raise ValueError("OpenDDE observation identity/count differs")
                            reported_error = result.iloc[0].get("error")
                            observation["status"] = "failed" if pd.notna(reported_error) else "complete"
                            if pd.notna(reported_error):
                                observation["error"] = str(reported_error)
                            return result
                        except (shell.ExecutionCancelled, KeyboardInterrupt, TimeoutError, subprocess.TimeoutExpired):
                            observation["status"] = "interrupted"
                            raise
                        except Exception as exc:
                            observation.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                            raise
                        finally:
                            observation["updated_utc"] = legacy.utc()
                            checkpoint()
                            _assert_source(source, source_hashes)
                    began = time.monotonic()
                    try:
                        with RunLock(run):
                            _msas(source, case["cfg"], case["target"], case["candidates"], case["msa_hashes"])
                            engine_comparison.run(cfg, run, RunState(run), copy.deepcopy(case["target"]), case["candidates"].copy(),
                                                  predict=predict, finalize=cli._finalize)
                            comparison = _json(run / "02_comparison/comparison_status.json")
                            scored = pd.read_csv(run / "03_score/scored_candidates.csv")
                            expected = {r["id"] for r in record["observations"]}
                            if set(scored["design_id"]) != expected or len(scored) != 4:
                                raise ValueError("Addition scored observation coverage differs")
                            evaluated = scored["final_evaluated"].eq(True) & ~scored.get("error", pd.Series(None, index=scored.index, dtype=object)).notna()
                            record.update(status=comparison["status"], models_evaluated=int(evaluated.sum()),
                                          scoring_failed=int((scored["prediction_status"].eq("complete") & ~evaluated).sum()))
                            if not evaluated.all():
                                record["status"] = "partial_failure"
                    except (shell.ExecutionCancelled, KeyboardInterrupt, TimeoutError, subprocess.TimeoutExpired):
                        raise
                    except Exception as exc:
                        record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                        _save(run / "failure_receipt.json", {"error": record["error"], "failed_utc": legacy.utc()})
                    _assert_source(source, source_hashes)
                    record.update(completed_utc=legacy.utc(),
                                  elapsed_seconds=time.monotonic() - began,
                                  prediction_rows=sum(o["status"] in {"complete", "failed"} for o in record["observations"]),
                                  prediction_completed=sum(o["status"] == "complete" for o in record["observations"]),
                                  prediction_failed=sum(o["status"] == "failed" for o in record["observations"]),
                                  target_receipt_sha256=provenance.file_digest(root / "targets" / (record["pdb_id"] + ".json")),
                                  artifact_sha256=legacy.terminal_hashes(run))
                    record["timings"]["msa_prediction_scoring_report_seconds"] = time.monotonic() - began
                    _save(run / "case_receipt.json", record)
                    checkpoint()
                _assert_source(source, source_hashes)
                checkpoint(status="complete" if all(r["status"] == "complete" for r in status["cases"]) else "partial_failure",
                           completed_utc=legacy.utc(), current_pdb=None, current_arm=None, current_engine=None, current_candidate=None)
                _save(root / "addition_complete.json", {"schema_version": 1, "status": status["status"],
                      "execution_fingerprint": provenance.fingerprint(identity),
                      "artifacts": _tree(root, exclude=(".run.lock", "addition_complete.json"))})
        except BaseException as exc:
            for record in status["cases"]:
                if record["status"] == "running":
                    record.update(status="interrupted", error=f"{type(exc).__name__}: {exc}", updated_utc=legacy.utc())
                    for observation in record["observations"]:
                        if observation["status"] == "running":
                            observation["status"] = "interrupted"
            checkpoint(status="interrupted", error=f"{type(exc).__name__}: {exc}")
            _save(root / "interruption_receipt.json", {"error": status["error"], "utc": legacy.utc()})
            raise
        finally:
            if previous_fd is None:
                os.environ.pop("NANOCOFOLD_GPU_LOCK_FD", None)
            else:
                os.environ["NANOCOFOLD_GPU_LOCK_FD"] = previous_fd
            if lock:
                lock.close()
        return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "root", "config"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--gpu")
    parser.add_argument("--budget-seconds", type=float, default=43200)
    args = parser.parse_args(argv)
    result = run_addition(args.source, args.root, args.config, gpu=args.gpu, budget_seconds=args.budget_seconds)
    print(json.dumps({key: result.get(key) for key in ("status", "completed_cases", "generated_candidates", "prediction_rows", "prediction_failed", "elapsed_seconds")}))


if __name__ == "__main__":
    main()
