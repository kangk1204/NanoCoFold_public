"""Sequential, engine-specific evaluation of the same reviewed VHH sequences.

Design engines supply sequences, never WT reproduction scores. Prediction
engines retain their own observations and ranking; no scores are averaged.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import time
from pathlib import Path

import pandas as pd

from . import provenance
from .config import cfg_get, cfg_seed, dump_resolved, resolve_path
from .utils import seq, shell

LEGACY_ENGINES = ("colabfold", "boltz2", "chai1")
ENGINES = (*LEGACY_ENGINES, "opendde")
AVAILABLE_ENGINES = ENGINES
SCHEMA_VERSION = 1


def enabled(cfg):
    return bool(cfg_get(cfg, "screen.comparison.enabled", False))


def settings(cfg):
    value = cfg_get(cfg, "screen.comparison", {})
    if not isinstance(value, dict) or type(value.get("enabled", False)) is not bool:
        raise ValueError("screen.comparison.enabled must be true/false")
    # A historical stored request may omit this field. New resolved configs
    # explicitly select four; missing fields must retain the original contract.
    engines = value.get("engines", list(LEGACY_ENGINES))
    if (not isinstance(engines, list) or not engines or any(not isinstance(e, str) or e not in AVAILABLE_ENGINES for e in engines)
            or len(set(engines)) != len(engines)):
        raise ValueError("screen.comparison.engines must contain unique colabfold|boltz2|chai1|opendde predictors")
    if "opendde" in engines:
        from .opendde import settings as opendde_settings
        opendde_settings(cfg, require_enabled=value.get("enabled", False))
    if type(value.get("continue_on_error", True)) is not bool:
        raise ValueError("screen.comparison.continue_on_error must be true/false")
    return {"enabled": value.get("enabled", False), "engines": engines,
            "continue_on_error": value.get("continue_on_error", True)}


def _save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def _msa_search_progress(state, payload, *, active=True):
    if state is None:
        return
    state.set_phase("comparison_msa_search", payload)
    # set_phase records a receipt and clears current_phase. Acquisition and
    # resource waits are still in progress until the exact MSA is available.
    if active and hasattr(state, "begin_phase"):
        state.begin_phase("comparison_msa_search")


def prepare_msa(cfg, run_dir, state, target, candidates, id_column="design_id", sequence_column="sequence"):
    """Freeze exact validated alignments for every chain, including generated VHHs.

    Local acquisition is the default online provider; public API acquisition
    requires explicit consent to upload the target and generated sequences.
    All predictors receive the same frozen, unpaired monomer inputs.
    """
    seq.candidate_id_aliases(candidates[id_column])
    if cfg_get(cfg, "run.mock", False) or cfg_get(cfg, "run.dry_run", False):
        return copy.deepcopy(cfg), candidates.copy(), {}
    from .colabfold_msa import cached_monomer, prepare_manifest, prepare_id_aliases
    from .screen import complex_chain_records
    copied, candidates = copy.deepcopy(cfg), candidates.copy()
    prepare_id_aliases(Path(run_dir) / "01_msa/comparison_id_aliases.json", candidates[id_column])
    cache = resolve_path(cfg, cfg_get(cfg, "screen.colabfold.msa_cache_dir", "msa/monomer_cache"))
    chains, _, binder = complex_chain_records(target, "")
    sequences = {**{"chain::" + letter: sequence for letter, sequence in chains.items() if letter != binder},
                 **{"candidate::" + str(row[id_column]): row[sequence_column] for row in candidates.to_dict("records")}}
    # Trusted local provider wiring lives in the shared MSA layer. The hook
    # receives reviewed sequences; arbitrary hosts/commands are never accepted.
    acquired = {}
    source = cfg_get(cfg, "screen.colabfold.msa_source", "cache")
    if source == "local_mmseqs2":
        acquired = _acquire_reviewed_local(copied, run_dir, state, sequences)
    elif source == "public_api":
        acquired = _acquire_reviewed_api(copied, run_dir, state, sequences)
    elif source != "cache":
        raise ValueError("Engine comparison MSA source must be cache, local_mmseqs2, or public_api")
    alignments = {}
    for identifier, sequence in sequences.items():
        try:
            alignments[identifier] = acquired.get(sequence) or cached_monomer(cache, sequence)
        except ValueError as exc:
            raise shell.ToolError(f"비교 실행에 필요한 {identifier}의 정확한 로컬 MSA가 없습니다: {exc}") from exc
    static = {letter: alignments["chain::" + letter] for letter in chains if letter != binder}
    parents = candidates[[id_column, sequence_column]].rename(columns={id_column: "design_id", sequence_column: "sequence"})
    parents = parents.copy()
    parents["cdr_ranges"] = "{}"
    templates, seen = [], set()
    for row in parents.to_dict("records"):
        if row["sequence"] not in seen:
            templates.append({"id": "exact_" + hashlib.sha256(row["sequence"].encode()).hexdigest(),
                              **alignments["candidate::" + row["design_id"]]})
            seen.add(row["sequence"])
    manifest = prepare_manifest(Path(run_dir) / "01_msa/comparison", static_chains=static, parents=parents,
                                public_templates=templates,
                                max_sequences=cfg_get(cfg, "screen.colabfold.msa_max_sequences", 2048))
    if "parent_id" in candidates:
        candidates["mutation_parent_id"] = candidates["parent_id"]
    if "parent_sequence" in candidates:
        candidates["mutation_parent_sequence"] = candidates["parent_sequence"]
    candidates["msa_parent_id"] = candidates[id_column].astype(str)
    candidates["parent_id"] = candidates[id_column].astype(str)
    candidates["parent_sequence"] = candidates[sequence_column]
    copied.setdefault("screen", {}).setdefault("colabfold", {}).update(
        msa_mode="precomputed", msa_dir=str(manifest.parent.resolve()), msa_manifest_sha256=provenance.file_digest(manifest))
    dump_resolved(copied, Path(run_dir) / "run_config.yaml")
    if state is not None:
        origins = [entry["receipt"].get("source_provenance", {}) for entry in alignments.values()]
        uploaded = any(origin.get("explicit_sequence_upload_authorized") is True for origin in origins)
        api_receipt = Path(run_dir) / "01_msa/api_comparison_acquisition.json"
        api = json.loads(api_receipt.read_text()) if api_receipt.is_file() else {}
        state.set_phase("comparison_msa", {"mode": "exact_cached_monomers", "pairing": "unpaired",
            "private_sequences_submitted_to_public_api": uploaded,
            "public_api_requests": api.get("public_api_requests", 0) if not uploaded or api else None,
            "request_counter_scope": api.get("request_counter_scope", "this_run; source cache history unspecified"),
            "provider": source,
            "source_providers": sorted({origin.get("provider", "unspecified") for origin in origins}),
            "manifest": str(manifest.relative_to(run_dir)),
            "manifest_sha256": provenance.file_digest(manifest), "chains": len(static), "parents": len(parents)})
    return copied, candidates, alignments


def _acquire_reviewed_api(cfg, run_dir, state, sequences):
    """Explicitly authorized exact sequences, serial batches and durable tickets."""
    from .colabfold_msa_acquire import acquire_authorized_monomers, HOST, PROVIDER
    if cfg_get(cfg, "screen.allow_public_msa_server", False) is not True:
        raise ValueError("public_api requires screen.allow_public_msa_server=true: target and generated sequences are uploaded")
    cache = resolve_path(cfg, cfg_get(cfg, "screen.colabfold.msa_cache_dir", "msa/api_authorized_cache"))
    queries = sorted(set(sequences.values()))
    # Keep source IDs stable across candidate renaming without claiming the
    # reviewed query is a publicly available source sequence.
    approved = {s: "authorized_" + hashlib.sha256(s.encode()).hexdigest() for s in queries}
    receipt_path = Path(run_dir) / "01_msa/api_comparison_acquisition.json"
    identity = {"provider": PROVIDER, "endpoint": "https://" + HOST,
                "sequence_sha256": sorted(hashlib.sha256(s.encode()).hexdigest() for s in queries),
                "authorization": "explicit_sequence_upload", "cache_dir": str(cache)}
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("identity") != identity:
            raise ValueError("API MSA inputs/authorization/cache changed; preserve this run and use a new analysis")
    else:
        receipt = {"schema_version": 1, "identity": identity, "status": "preparing", "batches": {}}
    _save(receipt_path, receipt)
    entries = {}

    def record_ticket_totals():
        # These counters survive failures between API calls and this run's
        # receipt write. Cache reuse may refer to an earlier run's acquisition;
        # its source history must never be presented as zero API requests.
        totals, ticket_ids = {"post": 0, "poll": 0, "download": 0}, []
        paths = list((cache / ".acquisition/tickets").glob("*/ticket.json"))
        if len(paths) > 10000:
            raise ValueError("Too many MSA acquisition receipts")
        for path in paths:
            ticket = json.loads(path.read_text())
            saved = ticket.get("identity", {})
            if (saved.get("provider") != PROVIDER or saved.get("authorization_kind") != "explicit_sequence_upload"
                    or not set(saved.get("queries", [])).intersection(queries)):
                continue
            for name in totals:
                value = ticket.get("requests", {}).get(name)
                if type(value) is not int or value < 0:
                    raise ValueError("API ticket request counters are damaged")
                totals[name] += value
            if ticket.get("ticket_id"):
                ticket_ids.append(ticket["ticket_id"])
        receipt.update(source_ticket_requests=totals, source_ticket_ids=sorted(set(ticket_ids)),
            public_api_requests=sum(totals.values()),
            request_counter_scope="source ticket lifetime; includes prior acquisition when reusing this cache")

    def budget():
        shell.remaining_budget()
        cancellation = shell._CANCELLATION.get()
        if cancellation and cancellation():
            raise shell.ExecutionCancelled("API MSA 준비가 취소되었습니다.")

    # One IP and serial batches satisfy the provider's request policy.
    for index in range(0, len(queries), 25):
        chunk = queries[index:index + 25]
        key = str(index // 25)
        budget()
        started = time.monotonic()
        _msa_search_progress(state, {"provider": PROVIDER, "status": "api_acquisition",
            "queries": len(chunk), "explicit_sequence_upload_authorized": True})
        try:
            result = acquire_authorized_monomers(cache, chunk, approved_sequences=approved,
                        allow_sequence_upload=True, allow_network=True, check_budget=budget)
        except (ValueError, OSError, TimeoutError, subprocess.TimeoutExpired, shell.ExecutionCancelled, KeyboardInterrupt) as exc:
            receipt.update(status="interrupted", error=str(exc))
            record_ticket_totals()
            _save(receipt_path, receipt)
            raise
        entries.update(result["entries"])
        old = receipt["batches"].get(key, {})
        request_keys = ("post_requests", "poll_requests", "download_requests")
        totals = {k: old.get("requests", {}).get(k, 0) + result["stats"][k] for k in request_keys}
        receipt["batches"][key] = {"requests": totals, "latest_stats": result["stats"],
            "elapsed_seconds": old.get("elapsed_seconds", 0) + time.monotonic() - started,
            "ticket_ids": sorted({e["receipt"]["source_provenance"]["ticket_id"] for e in result["entries"].values()}),
            "alignments": {hashlib.sha256(s.encode()).hexdigest(): e["sha256"] for s, e in result["entries"].items()}}
        receipt.update(status="complete" if len(entries) == len(queries) else "preparing",
            recorded_network_requests_this_run=sum(sum(b["requests"].values()) for b in receipt["batches"].values()),
            private_sequences_submitted_to_public_api=True)
        record_ticket_totals()
        _save(receipt_path, receipt)
    return entries


def _acquire_reviewed_local(cfg, run_dir, state, sequences):
    from .colabfold_msa_local import acquire_local_monomers, LocalMSATransientError
    from .colabfold_msa import cached_monomer
    cache = resolve_path(cfg, cfg_get(cfg, "screen.colabfold.msa_cache_dir", "msa/monomer_cache"))
    entries, origins = {}, {}
    for identifier, sequence in sequences.items():
        try:
            entries[sequence] = cached_monomer(cache, sequence)
            continue
        except ValueError:
            pass
        generated = identifier.startswith("candidate::") and cfg_get(cfg, "run.mode") == "design"
        origins.setdefault(sequence, {"source_id": "reviewed_" + hashlib.sha256(identifier.encode()).hexdigest()[:32],
                                      "query_kind": "generated_vhh" if generated else
                                      "reviewed_target" if identifier.startswith("chain::") else "reviewed_vhh"})
    chunks, current, residues = [], [], 0
    for sequence in sorted(origins):
        if current and (len(current) == 25 or residues + len(sequence) > 5000):
            chunks.append(current)
            current, residues = [], 0
        current.append(sequence)
        residues += len(sequence)
    if current:
        chunks.append(current)
    for index, chunk in enumerate(chunks):
        _msa_search_progress(state, {"provider": "registered_local_mmseqs", "batch": index + 1,
            "batches": len(chunks), "status": "acquisition", "queries": len(chunk), "public_api_requests": 0})
        while True:
            shell.remaining_budget()
            cancellation = shell._CANCELLATION.get()
            if cancellation and cancellation():
                raise shell.ExecutionCancelled("로컬 MSA 준비가 취소되었습니다.")
            try:
                result = acquire_local_monomers(cache, chunk, approved_base_sequences={s: origins[s] for s in chunk}, allow_search=True)
            except LocalMSATransientError as exc:
                # The next acquisition reconciles persisted submission intent
                # and revalidates DB identity before any search or prediction.
                info = {"provider": "registered_local_mmseqs", "batch": index + 1,
                        "batches": len(chunks), "status": "connection_wait",
                        "queries": len(chunk), "public_api_requests": 0, "reason": str(exc)}
                _save(Path(run_dir) / f"01_msa/local_connection_wait_{index}.json", info)
                _msa_search_progress(state, info)
                print("[MSA] 로컬 서버 연결 대기: " + str(exc), flush=True)
                time.sleep(5)
                continue
            _save(Path(run_dir) / f"01_msa/local_comparison_batch_{index}.json", result)
            transferred = set(chunk).issubset(result.get("entries", {}))
            _msa_search_progress(state, {"provider": "registered_local_mmseqs", "batch": index + 1,
                "batches": len(chunks), "status": "fetching" if result["status"] == "complete" and not transferred else result["status"],
                "completed_queries": len(result.get("entries", {})),
                "queries": len(chunk), "public_api_requests": 0},
                active=not (result["status"] == "complete" and transferred))
            if result["status"] == "complete" and transferred:
                entries.update(result["entries"])
                break
            # A pre-existing committed server ticket can return complete before
            # its files have been transferred into this local cache. The next
            # idempotent poll fetches those files; it does not launch a search.
            if result["status"] not in {"queued", "running", "busy", "resource_wait", "complete"}:
                raise shell.ToolError("로컬 MSA 서버 준비/검색 실패: " + json.dumps(result, ensure_ascii=False))
            time.sleep(5)
    return entries


def _conditions(cfg, engine):
    if engine == "opendde":
        from .opendde import settings as opendde_settings, SOURCE_COMMIT, VERSION
        return {"seed": cfg_seed(cfg, "run.seed"), "msa": "precomputed_unpaired", "restraint": False,
                "restraint_support": "unsupported; hotspots evaluated after prediction", "experimental": True,
                "source_commit": SOURCE_COMMIT, "package_version": VERSION, **opendde_settings(cfg)}
    restrained = bool(cfg_get(cfg, "screen.restraint.enabled", False)) and engine != "colabfold"
    if engine == "colabfold":
        return {"seed": cfg_seed(cfg, "run.seed"), "msa": "precomputed_unpaired", "restraint": False,
                "restraint_support": "unsupported; hotspots evaluated after prediction",
                "msa_max_sequences_per_chain": cfg_get(cfg, "screen.colabfold.msa_max_sequences", 2048),
                "num_recycles": cfg_get(cfg, "screen.colabfold.num_recycles", 1),
                "model_weights": cfg_get(cfg, "screen.colabfold.num_models", 1),
                "num_models": cfg_get(cfg, "screen.colabfold.num_models", 1) * cfg_get(cfg, "screen.colabfold.num_seeds", 1),
                "num_seeds": cfg_get(cfg, "screen.colabfold.num_seeds", 1)}
    if engine == "boltz2":
        return {"seed": cfg_seed(cfg, "score.boltz2.seed"), "msa": "precomputed_unpaired", "restraint": restrained,
                "restraint_type": "pocket" if restrained else None,
                "restraint_max_distance_A": cfg_get(cfg, "screen.boltz2_pocket_max_distance", 6.0) if restrained else None,
                "msa_subsampled_sequences": cfg_get(cfg, "score.boltz2_num_subsampled_msa", 512),
                "num_recycles": cfg_get(cfg, "score.boltz2_recycling_steps", 3),
                "diffusion_samples": cfg_get(cfg, "score.boltz2_diffusion_samples", 1),
                "extra_args": cfg_get(cfg, "score.boltz2_extra_args", [])}
    return {"seed": cfg_seed(cfg, "run.seed"), "msa": "precomputed_unpaired_aligned_parquet", "restraint": restrained,
            "restraint_type": cfg_get(cfg, "screen.restraint.connection_type", "pocket") if restrained else None,
            "restraint_max_distance_A": cfg_get(cfg, "screen.restraint.max_distance", 11.0) if restrained else None,
            "msa_max_sequences_per_chain": cfg_get(cfg, "screen.colabfold.msa_max_sequences", 2048),
            "msa_source_feature": "uniref90 compatibility bucket; merged original database labels unavailable",
            "num_recycles": cfg_get(cfg, "screen.num_trunk_recycles", 1),
            "diffusion_timesteps": cfg_get(cfg, "screen.num_diffn_timesteps", 50),
            "diffusion_samples": cfg_get(cfg, "screen.num_diffn_samples", 1)}


def run(cfg, run_dir, state, target, candidates, cdr_ranges=None, *, predict, finalize,
        id_column="design_id", sequence_column="sequence"):
    from .screen import _predictions_manifest_frame, complex_chain_records
    options = settings(cfg)
    candidates = candidates.copy()
    if cfg_get(cfg, "run.mode") == "design":
        from .pose_integration import preserve_generation_structure
        candidates = preserve_generation_structure(candidates)
    if candidates[id_column].astype(str).duplicated().any():
        raise ValueError("Comparison source candidate IDs must be unique")
    seq.validate_candidate_ids(candidates[id_column].astype(str))
    cfg, candidates, alignments = prepare_msa(cfg, run_dir, state, target, candidates, id_column, sequence_column)
    root = Path(run_dir) / "02_comparison"
    root.mkdir(parents=True, exist_ok=True)
    condition_identity = {"schema_version": SCHEMA_VERSION, "engines": options["engines"], "mock": bool(cfg_get(cfg, "run.mock", False)),
        "candidates": [{"id": str(r[id_column]), "sequence": r[sequence_column]} for r in candidates.to_dict("records")],
        "target": {key: target.get(key) for key in ("sequence", "aux_chains", "hotspot_seq_positions")},
        "conditions": {engine: _conditions(cfg, engine) for engine in options["engines"]},
        "screen_inference": {key: cfg_get(cfg, "screen." + key) for key in
            ("colabfold", "env_vars", "extra_args", "restraint", "num_trunk_recycles", "num_diffn_timesteps",
             "num_diffn_samples", "boltz2_pocket_max_distance", "env", "boltz2_env")},
        "boltz2_inference": {key: value for key, value in cfg_get(cfg, "score", {}).items() if key.startswith("boltz2")},
        "msa_hashes": {key: entry["sha256"] for key, entry in alignments.items()},
        "runtime": {engine: provenance.runtime_identity(cfg, engine) for engine in options["engines"]}
                   if not cfg_get(cfg, "run.mock", False) and not cfg_get(cfg, "run.dry_run", False) else {},
        "runner_sha256": provenance.file_digest(Path(__file__))}
    identity_path = root / "comparison_identity.json"
    identity = {"fingerprint": provenance.fingerprint(condition_identity), **condition_identity}
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise ValueError("Comparison input/engine/MSA conditions changed; preserve this run and submit a new analysis")
    _save(identity_path, identity)
    snapshot = {"schema_version": SCHEMA_VERSION, "engines": options["engines"], "mock": bool(cfg_get(cfg, "run.mock", False)),
                "input_candidates": len(candidates), "expected_rows": len(candidates) * len(options["engines"]),
                "score_aggregation": "none", "ranking_scope": "within each predictor engine", "status": "running", "rows": []}
    _save(root / "comparison_status.json", snapshot)
    all_rows = []
    for engine in options["engines"]:
        applied = copy.deepcopy(cfg)
        applied["screen"]["engine"] = engine
        if engine in {"colabfold", "opendde"}:
            applied["screen"].setdefault("restraint", {})["enabled"] = False
        for source in candidates.to_dict("records"):
            source_id = str(source[id_column])
            identifier = seq.validate_candidate_id(engine + "__" + source_id)
            query = {**source, id_column: identifier, "parent_id": source_id, "source_sequence_id": source_id,
                     "design_origin": source.get("design_engine", source.get("design_origin", source.get("source", "submitted"))),
                     "predictor_engine": engine, "engine": engine, "comparison": True,
                     "is_parent_control": source_id == "WT" or source.get("is_parent_control", False)}
            query.pop("pred_dir", None)
            target_for_engine = copy.deepcopy(target)
            chains, _, binder = complex_chain_records(target, source[sequence_column])
            if alignments:
                monomers = {letter: alignments["candidate::" + source_id if letter == binder else "chain::" + letter] for letter in chains}
                target_for_engine["target_msa"] = monomers["A"]["a3m"]
                target_for_engine["chain_msas"] = {letter: entry["a3m"] for letter, entry in monomers.items()}
                target_for_engine["binder_msa"] = monomers[binder]["a3m"]
            started = time.monotonic()
            try:
                if alignments and engine == "chai1":
                    from .screen import prepare_chai_precomputed_msa
                    directory = prepare_chai_precomputed_msa(applied, root / "chai_msa" / source_id, chains, monomers)
                    applied["screen"]["msa_mode"] = "precomputed"
                    applied["screen"].setdefault("chai1", {})["msa_directory"] = str(directory)
                result = predict(applied, root / engine, target_for_engine, pd.DataFrame([query]), engine=engine,
                                 mock=bool(cfg_get(cfg, "run.mock", False)), dry_run=bool(cfg_get(cfg, "run.dry_run", False)),
                                 id_column=id_column, sequence_column=sequence_column)
                if len(result) != 1 or str(result.iloc[0][id_column]) != identifier:
                    raise ValueError("Prediction result identity/count differs from comparison input")
                prediction = result.iloc[0].to_dict()
                # Adapter stage manifests describe only this call. The global
                # comparison manifest is the single observation source.
                local_manifest = root / engine / "predictions.csv"
                if local_manifest.exists():
                    receipt_dir = root / engine / "query_receipts"
                    receipt_dir.mkdir(exist_ok=True)
                    local_manifest.replace(receipt_dir / (identifier + ".csv"))
                reported_error = prediction.pop("error", None)
                row = {**query, **prediction, "engine": engine, "predictor_engine": engine,
                       "mock": bool(cfg_get(cfg, "run.mock", False)),
                       "prediction_status": "failed" if pd.notna(reported_error) else "complete"}
                if pd.notna(reported_error):
                    row["prediction_error"] = str(reported_error)
            except (shell.ExecutionCancelled, KeyboardInterrupt, TimeoutError, subprocess.TimeoutExpired):
                snapshot["status"] = "interrupted"
                _save(root / "comparison_status.json", snapshot)
                raise
            except Exception as exc:
                row = {**query, "pred_dir": None, "prediction_status": "failed", "prediction_error": f"{type(exc).__name__}: {exc}"}
                if not options["continue_on_error"]:
                    row.update(elapsed_seconds=time.monotonic() - started, **_conditions(applied, engine))
                    row["msa_requested"], row["msa"], row["final_evaluated"] = row["msa"], "not_evaluated", False
                    all_rows.append(row)
                    frame = pd.DataFrame(all_rows)
                    frame.to_csv(root / "comparison_candidates.csv", index=False)
                    _predictions_manifest_frame(frame, Path(run_dir)).to_csv(root / "predictions.csv", index=False)
                    snapshot["status"] = "failed"
                    snapshot["rows"].append({"id": identifier, "source_sequence_id": source_id, "engine": engine,
                                             "status": "failed", "error": row["prediction_error"]})
                    _save(root / "comparison_status.json", snapshot)
                    raise
            row.update(elapsed_seconds=time.monotonic() - started, **_conditions(applied, engine))
            # Every row needs an explicit value. A failure-only column makes
            # successful rows CSV NaN, which readers correctly treat as unknown.
            row["final_evaluated"] = row["prediction_status"] == "complete"
            if row["prediction_status"] == "failed":
                row["msa_requested"] = row["msa"]
                row["msa"] = "not_evaluated"
                row["final_evaluated"] = False
            row["comparison_conditions"] = json.dumps(_conditions(applied, engine), sort_keys=True)
            all_rows.append(row)
            snapshot["rows"].append({"id": identifier, "source_sequence_id": source_id, "engine": engine,
                                     "status": row["prediction_status"], "elapsed_seconds": row["elapsed_seconds"]})
            frame = pd.DataFrame(all_rows)
            frame.to_csv(root / "comparison_candidates.csv", index=False)
            # This manifest is intentionally complete across engines, including failures.
            _predictions_manifest_frame(frame, Path(run_dir)).to_csv(root / "predictions.csv", index=False)
            _save(root / "comparison_status.json", snapshot)
    snapshot["status"] = "partial_failure" if any(row["prediction_status"] == "failed" for row in all_rows) else "complete"
    _save(root / "comparison_status.json", snapshot)
    if state is not None:
        state.set_phase("comparison", {**snapshot, "dir": "02_comparison"})
        if snapshot["status"] == "partial_failure":
            state.add_warnings("예측 엔진 비교에서 일부 후보가 실패했습니다. 엔진별 실패 행과 원인을 보존했습니다.")
    if cfg_get(cfg, "run.dry_run", False):
        return {"selected": pd.DataFrame(), "scored": pd.DataFrame(), "report": None, "synthesis": None}
    return finalize(cfg, Path(run_dir), state, pd.DataFrame(all_rows), target, cdr_ranges, id_column, sequence_column)


def rank_and_select(frame, cfg, cdr_ranges=None, sequence_column="sequence", is_control=None):
    """Rank independently per engine and retain every observation in the report."""
    from . import rank_select
    ranked, selected = [], []
    for engine, group in frame.groupby("predictor_engine", sort=False):
        failures = group.get("error", pd.Series(None, index=group.index, dtype=object)).notna()
        failures |= group.get("prediction_status", pd.Series("complete", index=group.index)).eq("failed")
        values = rank_select.rank_candidates(group[~failures], cfg, cdr_ranges, sequence_column)
        values["engine_rank"] = values["rank"]
        values["ranking_scope"] = engine
        from .cascade import control_flags
        controls = control_flags(values)
        pool = values[~controls]
        picks = rank_select.select_final(pool, cfg, cdr_ranges, sequence_column,
                                        allow_fallback=bool(cfg_get(cfg, "rank.fallback_to_ineligible", False)),
                                        pre_ranked=True)
        picks["ranking_scope"] = engine
        ranked.append(values)
        unranked = group[failures].copy()
        unranked["composite_score"] = float("nan")
        unranked["rank"] = float("nan")
        unranked["engine_rank"] = float("nan")
        unranked["ranking_scope"] = engine
        ranked.append(unranked)
        selected.append(picks)
    if not ranked:
        return frame.copy(), frame.iloc[:0].copy()
    return pd.concat(ranked, ignore_index=True), pd.concat(selected, ignore_index=True)


def write_synthesis_by_engine(selected, cfg, final_dir, sequence_column="sequence"):
    from . import handoff
    metadata = ("predictor_engine", "source_sequence_id", "design_origin", "ranking_scope")
    outputs = {}
    for engine, frame in selected.groupby("predictor_engine", sort=False):
        directory = Path(final_dir) / ("engine_" + engine)
        directory.mkdir(parents=True, exist_ok=True)
        frame.to_csv(directory / "selected_candidates.csv", index=False)
        outputs[engine] = handoff.write_synthesis_sheet(frame, cfg, directory, sequence_column, metadata_columns=metadata)
    # Independent engine rankings do not decide a combined synthesis order.
    empty = selected.iloc[:0]
    summary = handoff.write_synthesis_sheet(empty, cfg, Path(final_dir), sequence_column)
    summary.update(status="comparison_requires_manual_sequence_selection", requires_researcher_selection=True,
                   ranking_scope="per_engine", engines=outputs)
    _save(Path(final_dir) / "comparison_selection.json", summary)
    return summary
