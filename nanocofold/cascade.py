"""Optional ColabFold screening followed by independently ranked Boltz-2 results.

ColabFold predicts structures; mutation generation remains the mature library's
responsibility. No numerical score is combined across the two engines.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
import re
import time
import subprocess
from pathlib import Path

import pandas as pd

from .utils.atomic import check_output, write_csv, write_json
from .config import cfg_get, dump_resolved, resolve_run_artifact, resolve_path
from .rank_select import cdr3_identity, guess_cdr3
from .utils.shell import ExecutionCancelled, ToolError
from .score_complex import iptm_missing_warnings

SCHEMA_VERSION = 1
FINAL_METRICS = ("ipsae", "iptm", "interface_plddt", "interface_pae")
VALID_METRICS = ("ipsae", "iptm")
PREDICTION_FIELDS = ("pred_dir", "engine", "target_chain", "binder_chain", "aux_chains")


def enabled(cfg: dict) -> bool:
    return bool(cfg_get(cfg, "screen.cascade.enabled", False))


def _cached_msa(cfg, sequence, label):
    from .colabfold_msa import cached_monomer
    cache = resolve_path(cfg, cfg_get(cfg, "screen.colabfold.msa_cache_dir", "msa/monomer_cache"))
    try:
        return cached_monomer(cache, sequence)
    except ValueError as exc:
        raise ToolError(f"{label}의 검증된 MSA 캐시가 없거나 손상되었습니다. 환경 화면에서 공개 MSA를 준비하거나 승인된 로컬 캐시를 설치하세요. ({exc})") from exc


def _static_msas(cfg, target):
    from .screen import complex_chain_records
    chains, _, binder = complex_chain_records(target, "")
    return {letter: _cached_msa(cfg, sequence, f"표적/파트너 체인 {letter}")
            for letter, sequence in chains.items() if letter != binder}


def approved_public_sequences(preset_id):
    path = Path(__file__).parent / "_data/public-msa-presets.json"
    catalog = json.loads(path.read_text())
    if (catalog.get("schema_version") != 1 or catalog.get("preset_id") != preset_id
            or catalog.get("private_generated_queries_allowed") is not False):
        raise ValueError("승인된 공개 MSA preset이 아닙니다.")
    approved = {}
    for identifier, entry in catalog.get("public_queries", {}).items():
        sequence = entry.get("sequence")
        if (not isinstance(sequence, str) or len(sequence) != entry.get("length")
                or hashlib.sha256(sequence.encode()).hexdigest() != entry.get("sequence_sha256")):
            raise ValueError("공개 MSA preset의 서열/해시 명세가 손상되었습니다.")
        approved[sequence] = identifier
    if not approved or len(approved) > 3:
        raise ValueError("공개 MSA preset의 입력 범위가 지원되지 않습니다.")
    return approved


def _save_msa_stats(cfg, run_dir, public_ids, latest_stats):
    path = Path(run_dir) / "01_msa/public_acquisition.json"
    previous = json.loads(path.read_text()) if path.is_file() else None
    if previous is not None and (not isinstance(previous, dict) or previous.get("schema_version") != 1
                                 or previous.get("preset_id") != cfg_get(cfg, "screen.colabfold.cf_public_preset")
                                 or not isinstance(previous.get("stats"), dict)):
        raise ValueError("저장된 공개 MSA 요청 명세가 현재 preset과 다릅니다. 새 실행을 사용하세요.")
    path.parent.mkdir(parents=True, exist_ok=True)
    stats = dict(latest_stats)
    peak_missing = stats.get("unique_missing", 0)
    if previous:
        for key in ("post_requests", "poll_requests", "download_requests"):
            stats[key] = stats.get(key, 0) + previous["stats"].get(key, 0)
        peak_missing = max(peak_missing, previous.get("unique_missing_peak", previous["stats"].get("unique_missing", 0)))
    _save_json(path, {"schema_version": 1, "preset_id": cfg_get(cfg, "screen.colabfold.cf_public_preset"),
                      "public_query_ids": public_ids,
                      "stats": stats, "latest_stats": latest_stats, "request_counter_scope": "run",
                      "unique_missing_peak": peak_missing,
                      "private_sequences_submitted": False})


def _acquire_public_msas(cfg, run_dir, sequences):
    source = cfg_get(cfg, "screen.colabfold.msa_source", "cache")
    sequences = sorted(set(sequences))
    cache = resolve_path(cfg, cfg_get(cfg, "screen.colabfold.msa_cache_dir", "msa/monomer_cache"))
    if source == "public_api":
        from .engine_comparison import _acquire_reviewed_api
        _acquire_reviewed_api(cfg, run_dir, None, {str(index): sequence for index, sequence in enumerate(sequences)})
        return
    if source == "cache":
        from .colabfold_msa import cached_monomer
        hits = 0
        for sequence in sequences:
            try:
                cached_monomer(cache, sequence)
                hits += 1
            except ValueError:
                pass  # Labeled cache errors are raised by the caller before inference.
        _save_msa_stats(cfg, run_dir, [], {"cache_hits": hits, "unique_missing": len(sequences) - hits,
                                         "post_requests": 0, "poll_requests": 0, "download_requests": 0,
                                         "source": "cache", "mode": "verified_local_cache"})
        return
    if source != "public_mmseqs2":
        raise ValueError("screen.colabfold.msa_source는 cache/public_mmseqs2 중 하나여야 합니다.")
    approved = approved_public_sequences(cfg_get(cfg, "screen.colabfold.cf_public_preset"))
    if any(sequence not in approved for sequence in sequences):
        raise ToolError("승인된 공개 preset과 다른 서열이 있습니다. 비공개/생성 서열은 MSA API로 전송하지 않습니다. 검증된 로컬 캐시를 사용하세요.")
    # Validate a prior receipt before starting a bounded network operation.
    path = Path(run_dir) / "01_msa/public_acquisition.json"
    if path.is_file():
        previous = json.loads(path.read_text())
        if (not isinstance(previous, dict) or previous.get("schema_version") != 1
                or previous.get("preset_id") != cfg_get(cfg, "screen.colabfold.cf_public_preset")
                or not isinstance(previous.get("stats"), dict)):
            raise ValueError("저장된 공개 MSA 요청 명세가 현재 preset과 다릅니다. 새 실행을 사용하세요.")
    from .colabfold_msa_acquire import acquire_public_monomers
    result = acquire_public_monomers(cache, sequences, approved_public_sequences=approved, allow_network=True)
    _save_msa_stats(cfg, run_dir, [approved[sequence] for sequence in sequences], result["stats"])


def preflight_msa(cfg, run_dir, target):
    """Cache-only design preflight; no private sequences leave this process."""
    if (cfg_get(cfg, "screen.colabfold.msa_mode") != "precomputed" or cfg_get(cfg, "run.mock", False)
            or cfg_get(cfg, "run.dry_run", False) or not cfg_get(cfg, "design.refinement.enabled", False)):
        return
    from .design_refinement import public_msa_templates, public_msa_template_sequences
    from .screen import complex_chain_records
    chains, _, binder = complex_chain_records(target, "")
    sources = public_msa_template_sequences(cfg)
    _acquire_public_msas(cfg, run_dir, [sequence for letter, sequence in chains.items() if letter != binder]
                         + [source["sequence"] for source in sources.values()])
    _static_msas(cfg, target)
    public_msa_templates(cfg, run_dir)


def _validate_msa_lineage(candidates, parents, id_column, sequence_column):
    from .colabfold_msa import AMINO
    controls = control_ids(candidates, id_column)
    for row in candidates.to_dict("records"):
        identifier = str(row[id_column])
        parent = parents.get(row.get("parent_id"))
        sequence = row.get(sequence_column)
        if parent is None or not isinstance(sequence, str) or not sequence or set(sequence) - AMINO:
            raise ValueError(f"후보 {identifier}의 MSA 부모/서열이 유효하지 않습니다.")
        baseline = parent["sequence"]
        recorded_parent = row.get("parent_sequence")
        if isinstance(recorded_parent, str) and recorded_parent != baseline:
            raise ValueError(f"후보 {identifier}의 기록된 부모 서열이 MSA 부모와 다릅니다.")
        if len(sequence) != len(baseline):
            raise ValueError(f"후보 {identifier}의 길이가 MSA 부모와 다릅니다. 부모별 CDR 치환만 지원합니다.")
        changed = {index for index, (left, right) in enumerate(zip(baseline, sequence), 1) if left != right}
        allowed = {index for start, end in parent.get("cdr_ranges", {}).values() for index in range(start, end + 1)}
        if len(changed) > 5 or not changed <= allowed or identifier in controls and changed:
            raise ValueError(f"후보 {identifier}는 부모의 CDR 내부 1–5개 치환/원본 대조 조건을 벗어났습니다.")
        declared = row.get("mutation_count")
        if declared is not None and not pd.isna(declared) and (isinstance(declared, bool) or float(declared) != len(changed)):
            raise ValueError(f"후보 {identifier}의 변이 개수 기록이 실제 부모 대비 치환과 다릅니다.")


def prepare_msa(cfg, run_dir, state, target, candidates, cdr_ranges=None, id_column="design_id", sequence_column="sequence"):
    """Freeze local parent/static MSAs before calibration or first-stage inference."""
    if cfg_get(cfg, "screen.colabfold.msa_mode") != "precomputed":
        return cfg, candidates
    if cfg_get(cfg, "run.mock", False) or cfg_get(cfg, "run.dry_run", False):
        if hasattr(state, "set_phase") and not cfg_get(cfg, "run.dry_run", False):
            state.set_phase("colabfold_msa", {"mode": "precomputed", "mock": True, "mock_bypass": True,
                                             "actual_homologs_evaluated": False})
        return cfg, candidates
    from .colabfold_msa import prepare_manifest
    from . import mature, provenance
    cfg, candidates = copy.deepcopy(cfg), candidates.copy()
    if hasattr(state, "begin_phase"):
        state.begin_phase("colabfold_msa")
    mode = cfg_get(cfg, "run.mode")
    if mode == "design" and cfg_get(cfg, "design.refinement.enabled", False):
        from .design_refinement import public_msa_templates, public_msa_template_sequences
        parents = pd.read_csv(Path(run_dir) / "02_design/refinement/parents.csv", dtype={"design_id": str})
        public_queries = [source["sequence"] for source in public_msa_template_sequences(cfg).values()]
    elif mode == "mature":
        public_queries = [mature.resolve_wt_sequence(cfg)]
    else:
        public_queries = candidates[sequence_column].tolist()
    from .screen import complex_chain_records
    chains, _, binder = complex_chain_records(target, "")
    _acquire_public_msas(cfg, run_dir, [sequence for letter, sequence in chains.items() if letter != binder] + public_queries)
    static = _static_msas(cfg, target)
    if mode == "design" and cfg_get(cfg, "design.refinement.enabled", False):
        templates = public_msa_templates(cfg, run_dir)
    elif mode == "mature":
        sequence = mature.resolve_wt_sequence(cfg)
        if not cdr_ranges:
            raise ValueError("mature MSA에는 명시적으로 검토된 WT CDR 범위가 필요합니다.")
        parents = pd.DataFrame([{"design_id": "WT", "sequence": sequence, "cdr_ranges": cdr_ranges}])
        source = _cached_msa(cfg, sequence, "WT VHH")
        templates = [{"id": "cached_wt", "a3m": source["a3m"], "sha256": source["sha256"], "cdr_ranges": cdr_ranges}]
        candidates["parent_id"] = "WT"
        candidates["parent_sequence"] = sequence
        candidates["cdr_ranges"] = json.dumps(cdr_ranges, sort_keys=True)
    else:
        # Independent submitted VHHs need exact cached monomer alignments.
        # Random libraries use mature/refinement to carry explicit parent lineage.
        parents = candidates[[id_column, sequence_column]].rename(columns={id_column: "design_id", sequence_column: "sequence"}).copy()
        parents["cdr_ranges"] = "{}"
        templates = []
        seen = set()
        for row in parents.to_dict("records"):
            if row["sequence"] in seen:
                continue
            source = _cached_msa(cfg, row["sequence"], f"독립 후보 {row['design_id']}")
            templates.append({"id": "cached_" + hashlib.sha256(row["sequence"].encode()).hexdigest()[:16],
                              "a3m": source["a3m"], "sha256": source["sha256"]})
            seen.add(row["sequence"])
        candidates["parent_id"] = candidates[id_column]
        candidates["parent_sequence"] = candidates[sequence_column]
    destination = Path(run_dir) / "01_msa/colabfold"
    manifest = prepare_manifest(destination, static_chains=static, parents=parents,
                                public_templates=templates,
                                max_sequences=cfg_get(cfg, "screen.colabfold.msa_max_sequences", 2048))
    _validate_msa_lineage(candidates, json.loads(manifest.read_text())["parents"], id_column, sequence_column)
    cfg.setdefault("screen", {}).setdefault("colabfold", {})["msa_dir"] = str(manifest.parent.resolve())
    cfg["screen"]["colabfold"]["msa_manifest_sha256"] = provenance.file_digest(manifest)
    dump_resolved(cfg, Path(run_dir) / "run_config.yaml")
    api_path = Path(run_dir) / "01_msa/api_comparison_acquisition.json"
    api = json.loads(api_path.read_text()) if api_path.is_file() else {}
    state.set_phase("colabfold_msa", {"mode": "precomputed", "mock": False, "parents": len(parents),
                                     "static_chains": len(static), "pairing": "unpaired",
                                     "manifest": "01_msa/colabfold/manifest.json",
                                     "manifest_sha256": cfg["screen"]["colabfold"]["msa_manifest_sha256"],
                                     "source": "public_api" if cfg_get(cfg, "screen.colabfold.msa_source") == "public_api" else "verified_local_cache",
                                     "private_sequences_submitted": api.get("private_sequences_submitted_to_public_api", False),
                                     "public_api_requests": api.get("public_api_requests", 0),
                                     "request_counter_scope": api.get("request_counter_scope", "this run; cache history unspecified")})
    return cfg, candidates


def control_flags(candidates, id_column="design_id"):
    flags = candidates[id_column].astype(str).eq("WT")
    if "is_parent_control" in candidates:
        flags |= candidates["is_parent_control"].map(lambda value: value is True or str(value).lower() == "true")
    return flags


def control_ids(candidates, id_column="design_id"):
    return set(candidates.loc[control_flags(candidates, id_column), id_column].astype(str))


def budget_config(cfg, candidates, id_column="design_id"):
    final_count = cfg_get(cfg, "screen.cascade.final_count")
    if final_count is None:
        return cfg
    controls = control_ids(candidates, id_column)
    if type(final_count) is not int or not 1 <= final_count <= 10000 or final_count <= len(controls):
        raise ValueError("cascade.final_count는 대조군 수보다 큰 1–10000 정수여야 합니다.")
    if int(cfg_get(cfg, "screen.cascade.audit_n", 0)) or int(cfg_get(cfg, "screen.cascade.diversity_n", 0)):
        raise ValueError("정확한 final_count는 audit_n=diversity_n=0이 필요합니다.")
    copied = copy.deepcopy(cfg)
    copied["screen"]["cascade"]["top_n"] = final_count - len(controls)
    return copied


def settings(cfg: dict) -> dict:
    value = cfg_get(cfg, "screen.cascade", {}) or {}
    return {"enabled": enabled(cfg), "top_n": int(value.get("top_n", 20)),
            "final_count": value.get("final_count"),
            "ranking_metric": value.get("ranking_metric", "iptm"),
            "diversity_n": int(value.get("diversity_n", 0)),
            "diversity_identity": float(value.get("diversity_identity", .85)),
            "audit_n": int(value.get("audit_n", 2)),
            "audit_seed": int(value.get("audit_seed") if value.get("audit_seed") is not None
                              else cfg_get(cfg, "run.seed", 42) or 0)}


def mature_prefilter_config(cfg: dict) -> dict:
    """Retain WT independently of the screen budget; do not change user config."""
    if not enabled(cfg):
        return cfg
    copied = copy.deepcopy(cfg)
    copied.setdefault("rank", {})["include_wt_control"] = True
    return copied


def _finite(value) -> bool:
    try:
        return not isinstance(value, bool) and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _error(value) -> bool:
    return value is not None and not pd.isna(value) and str(value).strip() not in {"", "None", "nan"}


def select_candidates(candidates: pd.DataFrame, metrics: pd.DataFrame, cfg: dict,
                      id_column: str = "design_id", sequence_column: str = "sequence",
                      cdr_ranges: dict | None = None) -> pd.DataFrame:
    """Deterministic top pool + WT + optional diverse and audit controls.

    A finite configured metric is required for top/diversity selection. WT and
    budgeted audit controls may also include unassessed first-stage candidates,
    so engine failures cannot become an invisible permanent exclusion.
    """
    params = settings(cfg)
    base = candidates.copy()
    base[id_column] = base[id_column].astype(str)
    if base[id_column].duplicated().any():
        raise ValueError("cascade 후보 ID가 중복되었습니다.")
    metrics = metrics.copy()
    if sequence_column in metrics:
        checked = base[[id_column, sequence_column]].merge(metrics[[id_column, sequence_column]], on=id_column,
                                                          how="left", suffixes=("", "_metric"), validate="one_to_one")
        provided = checked[sequence_column + "_metric"].notna()
        if not checked.loc[provided, sequence_column].equals(checked.loc[provided, sequence_column + "_metric"].rename(sequence_column)):
            raise ValueError("cascade 점수 원장의 후보 서열이 입력과 다릅니다.")
        metrics = metrics.drop(columns=[sequence_column])
    frame = base[[id_column, sequence_column]].merge(metrics, on=id_column, how="left", validate="one_to_one")
    metric = str(params["ranking_metric"])
    if metric not in frame:
        frame[metric] = float("nan")
    frame["_valid"] = frame[metric].map(_finite)
    if "error" in frame:
        frame["_valid"] &= ~frame["error"].map(_error)
    ascending = metric in {"interface_pae", "n_clashes"}
    valid = frame[frame["_valid"]].sort_values([metric, id_column], ascending=[ascending, True], kind="stable")
    ranks = {str(identifier): index + 1 for index, identifier in enumerate(valid[id_column])}
    reasons: dict[str, list[str]] = {str(identifier): [] for identifier in frame[id_column]}
    controls = control_ids(base, id_column)
    pool = valid[~valid[id_column].astype(str).isin(controls)]
    for identifier in pool.head(params["top_n"])[id_column]:
        reasons[str(identifier)].append("colabfold_top")
    for identifier in controls:
        reasons[identifier].append("wt_control" if identifier == "WT" else "parent_control")

    def region(sequence):
        if cdr_ranges and "CDR3" in cdr_ranges:
            start, end = cdr_ranges["CDR3"]
            return str(sequence)[int(start) - 1:int(end)]
        return guess_cdr3(str(sequence))

    representatives = [region(row[sequence_column]) for _, row in pool.iterrows() if reasons[str(row[id_column])]]
    added = 0
    for _, row in pool.iterrows():
        if added >= params["diversity_n"]:
            break
        identifier = str(row[id_column])
        if reasons[identifier]:
            continue
        segment = region(row[sequence_column])
        if all(cdr3_identity(segment, previous) < params["diversity_identity"] for previous in representatives):
            reasons[identifier].append("diversity_control")
            representatives.append(segment)
            added += 1
    outside = [str(identifier) for identifier in frame[id_column] if not reasons[str(identifier)]]
    # Hash ordering is stable across pandas, Python RNG versions and row order.
    outside.sort(key=lambda identifier: (hashlib.sha256(
        f"{params['audit_seed']}:{identifier}".encode()).hexdigest(), identifier))
    for identifier in outside[:params["audit_n"]]:
        reasons[identifier].append("audit_outside_top" if identifier in ranks else "audit_colabfold_unassessed")
    rows = []
    for _, row in frame.iterrows():
        identifier = str(row[id_column])
        reason = ";".join(reasons[identifier])
        if not reason:
            reason = "outside_boltz_budget" if bool(row["_valid"]) else "colabfold_metric_unavailable"
        rows.append({id_column: identifier, "colabfold_rank": ranks.get(identifier),
                     "selected_for_boltz": bool(reasons[identifier]),
                     "cascade_selection_reason": reason, "cascade_ranking_metric": metric,
                     "cascade_ranking_value": float(row[metric]) if _finite(row[metric]) else None})
    return pd.DataFrame(rows)


def _read_manifest(run_dir: Path, stage: Path, id_column: str) -> pd.DataFrame:
    path = stage / "predictions.csv"
    if not path.exists():
        return pd.DataFrame(columns=[id_column, *PREDICTION_FIELDS])
    frame = pd.read_csv(path, dtype={id_column: str})
    if id_column not in frame or frame[id_column].duplicated().any():
        raise ValueError(f"cascade prediction manifest ID 손상: {path}")
    frame[id_column] = frame[id_column].astype(str)
    if "pred_dir" in frame:
        frame["pred_dir"] = frame["pred_dir"].map(
            lambda value: str(resolve_run_artifact(run_dir, value)) if not pd.isna(value) else value)
    return frame


def _predict(cfg, run_dir, stage, target, candidates, engine, predict, state,
             id_column, sequence_column):
    if candidates.empty:
        return pd.DataFrame(columns=[id_column, *PREDICTION_FIELDS])
    # A manifest from an earlier attempt is not proof that this attempt checked
    # current inputs and output hashes. Keep it for audit, never salvage it.
    manifest = stage / "predictions.csv"
    if manifest.exists():
        archive = stage / ".history"
        archive.mkdir(parents=True, exist_ok=True)
        manifest.replace(archive / f"predictions.{uuid.uuid4().hex}.csv")
    try:
        return predict(cfg, stage, target, candidates, engine=engine,
                       mock=bool(cfg_get(cfg, "run.mock", False)), dry_run=False,
                       id_column=id_column, sequence_column=sequence_column)
    except ExecutionCancelled:
        raise
    except ToolError as exc:
        # Preserve prior validated completions; never turn cancellation into a
        # successful partial batch or catch programming/configuration errors.
        state.add_warnings(f"cascade {engine} 부분 실패: {exc}")
        return _read_manifest(run_dir, stage, id_column)


def _merge_predictions(candidates, predictions, id_column):
    stripped = candidates.drop(columns=[c for c in PREDICTION_FIELDS if c in candidates])
    if predictions.empty:
        result = stripped.copy()
        result["pred_dir"] = None
        return result
    predictions = predictions.copy()
    predictions[id_column] = predictions[id_column].astype(str)
    columns = [id_column, *[c for c in predictions if c != id_column and c not in stripped]]
    return stripped.merge(predictions[columns], on=id_column, how="left", validate="one_to_one")


def _portable(run_dir: Path, frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    for column in frame:
        if column.endswith("pred_dir") or column.endswith("cif_path"):
            def convert(value):
                if pd.isna(value) or not value:
                    return None
                path = resolve_run_artifact(run_dir, value)
                try:
                    return str(path.resolve().relative_to(run_dir.resolve()))
                except ValueError:
                    return str(path)
            frame[column] = pd.Series([convert(value) for value in frame[column]], index=frame.index, dtype="object")
    return frame


def _stage_table(candidates, predictions, metrics, prefix, id_column):
    linked = _merge_predictions(candidates, predictions, id_column)
    # Candidate source scores are preserved, but engine metrics are isolated.
    columns = {c: prefix + c for c in metrics if c != id_column}
    score_frame = metrics.rename(columns=columns)
    prediction_columns = set(linked) - set(candidates) - {id_column}
    prediction_columns.update(c for c in PREDICTION_FIELDS if c in linked)
    linked = linked.rename(columns={c: prefix + c for c in prediction_columns})
    linked = linked.drop(columns=[c for c in score_frame if c != id_column and c in linked])
    return linked.merge(score_frame, on=id_column, how="left", validate="one_to_one")


def _save_json(path, payload):
    write_json(path, payload)


def _publish(cfg, run_dir, state, target, candidates, first, selection, boltz_predictions,
             score, finalize, cdr_ranges, id_column, sequence_column, reanalysis=False):
    score_dir, final_dir = run_dir / "03_score", run_dir / "05_final"
    score_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)
    chosen_ids = set(selection.loc[selection["selected_for_boltz"].eq(True), id_column].astype(str))
    chosen = candidates[candidates[id_column].astype(str).isin(chosen_ids)].copy()
    linked = _merge_predictions(chosen, boltz_predictions, id_column)
    boltz_metrics = score(cfg, linked, target, id_column)
    for column in FINAL_METRICS:
        if column not in boltz_metrics:
            boltz_metrics[column] = float("nan")
    # No contacts is a valid evaluated outcome: its interface-only values are
    # undefined. Require validated confidence/PAE metrics, preserve those nulls
    # and let the existing eligibility checks explain the absent interface.
    valid = boltz_metrics[list(VALID_METRICS)].map(_finite).all(axis=1)
    if "error" in boltz_metrics:
        valid &= ~boltz_metrics["error"].map(_error)
    valid_ids = set(boltz_metrics.loc[valid, id_column].astype(str))
    valid_candidates = linked[linked[id_column].astype(str).isin(valid_ids)].copy()
    boltz_stage = _stage_table(chosen, boltz_predictions, boltz_metrics, "boltz_", id_column)
    # Only valid Boltz structures reach ranking. CF-only candidates and failed
    # Boltz candidates cannot affect final normalization or receive a rank.
    gate_exit = None
    if len(valid_candidates):
        try:
            finalized = finalize(cfg, run_dir, state, valid_candidates, target, cdr_ranges,
                                 id_column, sequence_column, publish_report=False)
        except SystemExit as exc:
            # The existing handoff gate writes its results then exits(3). Full
            # first-stage records still need publication before that exit.
            if exc.code != 3:
                raise
            gate_exit = exc
            finalized = {"scored": pd.read_csv(score_dir / "scored_candidates.csv", dtype={id_column: str}),
                         "selected": pd.read_csv(final_dir / "selected_candidates.csv", dtype={id_column: str})}
        except (Exception, KeyboardInterrupt) as exc:
            current = state.get_phase("final")
            if not current.get("tables_committed"):
                raise
            gate_exit = exc
            finalized = {"scored": pd.read_csv(score_dir / "scored_candidates.csv", dtype={id_column: str}),
                         "selected": pd.read_csv(final_dir / "selected_candidates.csv", dtype={id_column: str})}
        if finalized.get("gate_blocks"):
            gate_exit = SystemExit(3)
        ranked = finalized["scored"].copy()
        ranked["final_evaluated"] = True
        ranked["final_rank"] = ranked["rank"]
        ranked["structure_stage"] = "boltz2"
        selected = finalized["selected"].copy()
    else:
        previous = state.get_phase("final")
        # Archive obsolete exports; an empty new pool must not inherit an old
        # failed gate or order list. Historical evidence remains downloadable.
        from uuid import uuid4
        history = run_dir / ".history" / ("empty-cascade-" + uuid4().hex)
        obsolete = [final_dir / name for name in (
            "handoff_gate.json", "synthesis_plate_list.csv", "synthesis_plate_list.xlsx", "top_candidates.pml")
            if (final_dir / name).is_file()]
        # Validate the entire move before touching any prior artifact. History
        # is an output path too and must not traverse a symlink below run root.
        for old in obsolete:
            check_output(old, root=run_dir)
            check_output(history / old.name, root=run_dir)
        if obsolete:
            history.mkdir(parents=True, exist_ok=True)
            for old in obsolete:
                old.replace(history / old.name)
        state.set_phase("final", {"previous_publication": {
            k: v for k, v in previous.items() if k != "previous_publication"},
            "tables_committed": False, "exports_status": "not_applicable",
            "report_status": "blocked", "delivery_status": "blocked",
            "synthesis": None, "handoff_gate": None, "n_selected": 0,
            "empty_final_pool": True})
        ranked = pd.DataFrame(columns=[id_column, "rank", "final_rank", "composite_score", "final_eligible"])
        selected = ranked.copy()
        write_csv(final_dir / "selected_candidates.csv", selected, root=run_dir)
        state.add_warnings("cascade: 유효한 Boltz-2 평가가 없어 최종 랭킹은 미평가입니다.")

    # First-stage table owns the complete candidate population.
    full = first.merge(selection, on=id_column, how="left", validate="one_to_one")
    final_columns = [id_column, *[c for c in ranked if c != id_column and c not in candidates]]
    full = full.merge(ranked[final_columns], on=id_column, how="left", validate="one_to_one")
    bolt_columns = [id_column, *[c for c in boltz_stage if c.startswith("boltz_")]]
    full = full.merge(boltz_stage[bolt_columns], on=id_column, how="left", validate="one_to_one")
    full["final_evaluated"] = full[id_column].astype(str).isin(valid_ids)
    for column in ("rank", "final_rank", "composite_score", "final_eligible", "error", "exclusion_reason", "cif_path", "pred_dir", "engine", "target_chain", "binder_chain"):
        if column not in full:
            full[column] = None
        if column not in {"rank", "final_rank", "composite_score"}:
            full[column] = full[column].astype("object")
    for index, row in full.iterrows():
        if row["final_evaluated"]:
            continue
        failed = bool(row["selected_for_boltz"])
        first_error = row.get("colabfold_error")
        full.at[index, "final_eligible"] = None
        full.at[index, "exclusion_reason"] = "boltz_failed_or_incomplete" if failed else "first_stage_only"
        if failed:
            error = row.get("boltz_error")
            full.at[index, "error"] = str(error) if _error(error) else "Boltz-2 필수 지표 또는 검증된 구조가 없습니다."
        elif _error(first_error):
            full.at[index, "error"] = str(first_error)
        full.at[index, "structure_stage"] = "colabfold"
        for column in PREDICTION_FIELDS + ("cif_path",):
            full.at[index, column] = row.get("colabfold_" + column)
    for column in FINAL_METRICS:
        if column not in full:
            full[column] = None
    full = full.sort_values(["final_rank", id_column], na_position="last", kind="stable").reset_index(drop=True)
    full = _portable(run_dir, full)
    from .pose_integration import annotate_frame
    full, pose_receipt = annotate_frame(full, cfg, target, run_dir, id_column, sequence_column)
    if pose_receipt.get("error"):
        state.add_warnings("자세 진단 미평가: " + pose_receipt["error"]["reason"])
    write_csv(score_dir / "scored_candidates.csv", full, root=run_dir)
    write_csv(final_dir / "ranked_candidates.csv", full, root=run_dir)
    write_csv(score_dir / "boltz_scores.csv", _portable(run_dir, boltz_stage), root=run_dir)
    if not len(valid_candidates):
        from .rank_select import ranking_policy
        _save_json(score_dir / "ranking_policy.json", {
            "method": "not_applied_no_valid_final_candidates",
            "ranking_scope": "valid_boltz_evaluations_only", "ranked_count": 0,
            "configured_policy": ranking_policy(cfg),
            "cross_engine_scores_combined": False, "calibrated_binding_probability": False,
        })
    # Selected export needs the same stage provenance without changing its
    # existing selection/synthesis eligibility policy.
    if len(selected):
        staged = full.set_index(id_column)
        for column in [c for c in full if c.startswith(("colabfold_", "boltz_", "cascade_", "pose_", "review_", "design_consistency", "design_binder", "site_", "candidate_review")) or c in {"final_evaluated", "final_rank", "structure_stage", "selected_for_boltz"}]:
            selected[column] = selected[id_column].map(staged[column])
        write_csv(final_dir / "selected_candidates.csv", _portable(run_dir, selected), root=run_dir)
    pose_receipt["source_csv_sha256"] = hashlib.sha256((score_dir / "scored_candidates.csv").read_bytes()).hexdigest()
    write_json(score_dir / "pose_assessment.json", pose_receipt, root=run_dir)
    n_first_failures = int(first.get("colabfold_error", pd.Series(dtype="object")).map(_error).sum())
    n_boltz_failures = len(chosen) - len(valid_ids)
    selection_manifest = score_dir / "cascade_selection.json"
    selection_provenance = json.loads(selection_manifest.read_text()) if selection_manifest.exists() else None
    summary = {"schema_version": SCHEMA_VERSION, "mode": cfg_get(cfg, "run.mode"),
               "engines": ["colabfold", "boltz2"], "settings": settings(cfg),
               "selection_provenance": selection_provenance,
               "counts": {"candidates": len(candidates), "colabfold_scored": len(first) - n_first_failures,
                          "colabfold_failed": n_first_failures, "selected_for_boltz": len(chosen),
                          "boltz_evaluated": len(valid_ids), "boltz_failed_or_incomplete": n_boltz_failures,
                          "first_stage_only": len(candidates) - len(chosen), "final_selected": len(selected)},
               "status": "partial_failure" if n_first_failures or n_boltz_failures else "complete",
               "reanalysis": reanalysis,
               "ranking_scope": "valid_boltz_evaluations_only",
               "cross_engine_scores_combined": False,
               "selection_recomputed": not reanalysis,
               "mock": bool(cfg_get(cfg, "run.mock", False))}
    if isinstance(gate_exit, (ExecutionCancelled, TimeoutError, subprocess.TimeoutExpired, KeyboardInterrupt)):
        summary["status"] = "interrupted"
    elif gate_exit is not None:
        summary["status"] = "partial_failure"
    _save_json(score_dir / "cascade_summary.json", summary)
    from . import report
    context = {"run_id": run_dir.name, "mode": cfg_get(cfg, "run.mode"),
               "num_candidates": len(full), "warnings": state.warnings,
               "num_clusters": int(selected["cluster_id"].nunique()) if "cluster_id" in selected else 0,
               "hotspots": target.get("hotspot_labels", []), "target_qc": target.get("qc", {}),
               "cascade": summary, "handoff_gate": state.get_phase("final").get("handoff_gate")}
    state.set_phase("cascade", summary)
    final_record = {**state.get_phase("final"), "scored_csv": str(score_dir / "scored_candidates.csv"),
                    "selected_csv": str(final_dir / "selected_candidates.csv"),
                    "report": None, "report_status": "pending", "n_selected": len(selected), "cascade": True,
                    "tables_committed": True}
    from .publication import table_hashes
    final_record["table_sha256"] = table_hashes(run_dir)
    if summary["status"] == "interrupted":
        final_record.update(report_status="interrupted", delivery_status="blocked")
    state.set_phase("final", final_record)
    if gate_exit is not None and not isinstance(gate_exit, SystemExit):
        raise gate_exit
    dump_resolved(cfg, run_dir / "run_config.yaml")
    try:
        report_path = report.generate_report(run_dir, cfg, context, scored=full, selected=selected)
    except Exception as exc:
        final_record.update(report_status="failed", report_error=f"{type(exc).__name__}: {exc}")
        state.set_phase("final", final_record)
        raise
    final_record.update(report=str(report_path), report_status="complete")
    state.set_phase("final", final_record)
    if not valid_ids:
        raise ToolError("cascade 결과를 보존했습니다. 유효한 Boltz-2 평가가 0개라 최종 랭킹을 완료하지 못했습니다.")
    if gate_exit is not None:
        raise gate_exit
    return {"selected": selected, "scored": full, "report": report_path, "summary": summary}


def run(cfg, run_dir, state, target, candidates, cdr_ranges, *, predict, score, finalize,
        id_column="design_id", sequence_column="sequence"):
    if str(cfg_get(cfg, "screen.engine", "")).lower() != "colabfold":
        raise ValueError("cascade는 screen.engine=colabfold가 필요합니다.")
    if cfg_get(cfg, "screen.restraint.enabled", False):
        raise ValueError("ColabFold cascade에는 screen.restraint.enabled=false가 필요합니다. hotspot 접촉은 결과에서 검토하세요.")
    if cfg_get(cfg, "run.mode") not in {"screen", "mature"} and not (
        cfg_get(cfg, "run.mode") == "design" and cfg_get(cfg, "design.refinement.enabled", False)):
        raise ValueError("cascade는 screen/mature 또는 design.refinement 모드만 지원합니다.")
    if candidates.empty:
        raise ValueError("cascade 구조 평가 후보가 없습니다.")
    candidates = candidates.copy()
    candidates[id_column] = candidates[id_column].astype(str)
    if candidates[id_column].duplicated().any():
        raise ValueError("cascade 후보 ID가 중복되었습니다.")
    cfg = budget_config(cfg, candidates, id_column)
    cfg, candidates = prepare_msa(cfg, run_dir, state, target, candidates, cdr_ranges, id_column, sequence_column)
    stage = run_dir / ("02b_mature_complex" if cfg_get(cfg, "run.mode") == "mature" else "02_screen")
    if cfg_get(cfg, "run.dry_run", False):
        predict(cfg, stage, target, candidates, engine="colabfold", mock=bool(cfg_get(cfg, "run.mock", False)),
                dry_run=True, id_column=id_column, sequence_column=sequence_column)
        return {"dry_run": True}
    score_dir = run_dir / "03_score"
    score_dir.mkdir(parents=True, exist_ok=True)
    write_csv(score_dir / "cascade_candidates.csv", candidates, root=run_dir)
    controls = control_ids(candidates, id_column)
    if cfg_get(cfg, "screen.cascade.calibrate_wt", False) and controls:
        state.begin_phase("runtime_calibration")
        control_id = "WT" if "WT" in controls else sorted(controls)[0]
        control = candidates[candidates[id_column] == control_id].copy()
        begin = time.monotonic()
        controls = _predict(cfg, run_dir, run_dir / "02c_boltz2", target, control, "boltz2", predict, state, id_column, sequence_column)
        control_metrics = score(cfg, _merge_predictions(control, controls, id_column), target, id_column)
        seconds = time.monotonic() - begin
        log = run_dir / "02c_boltz2" / control_id / "boltz2.log"
        if log.is_file():
            values = re.findall(r"# exit=0 elapsed=([\d.]+)s", log.read_text()[-2048:])
            if values:
                seconds = float(values[-1])
        valid = not control_metrics.empty and _finite(control_metrics.iloc[0].get("iptm")) and _finite(control_metrics.iloc[0].get("ipsae"))
        calibration = {"schema_version": 1, "engine": "boltz2", "candidate_id": control_id,
                       "control_kind": "wt_control" if control_id == "WT" else "parent_control", "valid": valid,
                       "mock": bool(cfg_get(cfg, "run.mock", False)), "seconds_per_candidate": seconds if valid else None,
                       "settings": cfg.get("score", {}), "purpose": "within_run_elapsed_time_estimate", "final_control_reuses_verified_cache": True}
        _save_json(score_dir / "cascade_calibration.json", calibration)
        state.set_phase("runtime_calibration", calibration)
    state.begin_phase("colabfold_screen")
    if cfg_get(cfg, "screen.cascade.prune_unselected", False):
        from .compact_screen import predict_and_score
        predictions, metrics = predict_and_score(cfg, stage, target, candidates, predict, score, id_column, sequence_column)
    else:
        predictions = _predict(cfg, run_dir, stage, target, candidates, "colabfold", predict, state, id_column, sequence_column)
        metrics = score(cfg, _merge_predictions(candidates, predictions, id_column), target, id_column)
    first = _stage_table(candidates, predictions, metrics, "colabfold_", id_column)
    state.add_warnings(iptm_missing_warnings(metrics.to_dict("records")))
    write_csv(score_dir / "colabfold_scores.csv", _portable(run_dir, first), root=run_dir)
    selection = select_candidates(candidates, metrics, cfg, id_column, sequence_column, cdr_ranges)
    write_csv(score_dir / "cascade_selection.csv", selection, root=run_dir)
    _save_json(score_dir / "cascade_selection.json", {
        "schema_version": SCHEMA_VERSION, "settings": settings(cfg),
        "candidate_sequences_sha256": hashlib.sha256(json.dumps(
            sorted(zip(candidates[id_column], candidates[sequence_column])), separators=(",", ":")).encode()).hexdigest(),
        "colabfold_scores_sha256": hashlib.sha256((score_dir / "colabfold_scores.csv").read_bytes()).hexdigest(),
        "selection_csv_sha256": hashlib.sha256((score_dir / "cascade_selection.csv").read_bytes()).hexdigest()})
    selected_ids = set(selection.loc[selection["selected_for_boltz"], id_column])
    selected = candidates[candidates[id_column].isin(selected_ids)].copy()
    state.set_phase("colabfold_screen", {"n": len(candidates), "selected_for_boltz": len(selected),
                                        "scores": str(score_dir / "colabfold_scores.csv")})
    print(f"[cascade] ColabFold {len(candidates)}종 → Boltz-2 {len(selected)}종 (원본/다양성/감사 대조 포함)")
    state.begin_phase("boltz_rescore")
    boltz = _predict(cfg, run_dir, run_dir / "02c_boltz2", target, selected, "boltz2", predict, state, id_column, sequence_column)
    return _publish(cfg, run_dir, state, target, candidates, first, selection, boltz,
                    score, finalize, cdr_ranges, id_column, sequence_column)


def reanalyze(cfg, run_dir, state, target, cdr_ranges, *, score, finalize):
    """Rescore saved structures; preserve the original budget/selection decision."""
    score_dir = run_dir / "03_score"
    candidates = pd.read_csv(score_dir / "cascade_candidates.csv", dtype={"design_id": str})
    selection = pd.read_csv(score_dir / "cascade_selection.csv", dtype={"design_id": str})
    stage = run_dir / ("02b_mature_complex" if cfg_get(cfg, "run.mode") == "mature" else "02_screen")
    predictions = _read_manifest(run_dir, stage, "design_id")
    if (stage / "compact_manifest.json").is_file():
        from . import provenance
        manifest = json.loads((stage / "compact_manifest.json").read_text())
        if provenance.fingerprint(cfg.get("score", {})) != manifest["inputs"]["score_config_fingerprint"]:
            raise ValueError("삭제된 1차 구조는 다른 점수 조건으로 재평가할 수 없습니다. 새 실행이 필요합니다.")
        pointer = json.loads((stage / "compact_checkpoint.json").read_text())
        snapshot = resolve_run_artifact(run_dir, str(stage.relative_to(run_dir) / ".compact/commits" / pointer["commit"]))
        receipt_path = snapshot / "receipt.json"
        receipt = json.loads(receipt_path.read_text())
        if provenance.file_digest(receipt_path) != pointer["receipt_sha256"] or receipt["fingerprint"] != manifest["fingerprint"]:
            raise ValueError("compact 점수 출처 해시가 손상되었습니다.")
        for name in ("compact_scores.csv", "predictions.csv"):
            if provenance.file_digest(stage / name) != receipt["files"][name]:
                raise ValueError("compact 점수 원장 해시가 손상되었습니다.")
        metrics = pd.read_csv(stage / "compact_scores.csv", dtype={"design_id": str})
        if "cif_path" in metrics:
            metrics["cif_path"] = metrics["cif_path"].map(lambda value: str(resolve_run_artifact(run_dir, value)) if isinstance(value, str) and value else value)
    else:
        metrics = score(cfg, _merge_predictions(candidates, predictions, "design_id"), target, "design_id")
    first = _stage_table(candidates, predictions, metrics, "colabfold_", "design_id")
    state.add_warnings(iptm_missing_warnings(metrics.to_dict("records")))
    write_csv(score_dir / "colabfold_scores.csv", _portable(run_dir, first), root=run_dir)
    boltz = _read_manifest(run_dir, run_dir / "02c_boltz2", "design_id")
    return _publish(cfg, run_dir, state, target, candidates, first, selection, boltz,
                    score, finalize, cdr_ranges, "design_id", "sequence", reanalysis=True)
