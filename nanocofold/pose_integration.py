"""Attach versioned structural diagnostics without replacing recorded scores.

Inference, legacy eligibility, engine ranks and native-reference diagnostics
remain separate. Report reassessment is CPU-only and never writes its source.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pandas as pd

from .platform.paths import native
from .utils import shell

_INTERRUPTIONS = (shell.ExecutionCancelled, KeyboardInterrupt, TimeoutError, subprocess.TimeoutExpired)


def _portable_details(value, paths):
    """Keep coordinate identity while removing runtime paths from diagnostics."""
    if isinstance(value, dict):
        return {key: _portable_details(item, paths) for key, item in value.items()}
    if isinstance(value, list):
        return [_portable_details(item, paths) for item in value]
    if isinstance(value, str):
        for runtime, portable in sorted(paths.items(), key=lambda item: len(item[0]), reverse=True):
            value = value.replace(runtime, portable)
    return value


def _unassessed(inputs, cfg, error):
    """An optional diagnostic failure cannot erase authoritative score tables."""
    from .pose_assessment import SCHEMA_VERSION, _site, settings
    policy = settings(cfg)
    reason = f"{type(error).__name__}: {error}"
    models = []
    for index, row in enumerate(inputs):
        site = _site(row)
        models.append({"model_id": row.get("model_id"), "input_index": index,
                       "site_satisfied": site["satisfied"], "site_selection_eligible": site["selection_eligible"],
                       "pose_cluster_id": None, "pose_supporting_engines": None,
                       "pose_quality_supporting_engines": None, "pose_consensus_status": "not_assessed",
                       "design_consistency_status": "not_assessed", "design_binder_rmsd_A": None,
                       "review_rank": None, "review_rank_scope": None,
                       "review_priority": "geometry_not_assessed", "review_priority_reason": "geometry_not_assessed",
                       "candidate_review_priority": "geometry_not_assessed",
                       "pose_assessment": {"status": "not_assessed", "reason": reason, "site": site,
                                           "policy_version": policy["version"], "policy_sha256": policy["sha256"],
                                           "review_priority": "geometry_not_assessed"}})
    return {"schema_version": SCHEMA_VERSION, "status": "not_assessed", "policy": policy,
            "error": {"type": type(error).__name__, "reason": reason},
            "models": models, "candidates": [], "pairs": [],
            "ranking_rule": {"version": "pose_review_rank_v3", "score_aggregation": "none", "calibrated": False},
            "interpretation": "Optional pose diagnostics unavailable; recorded scores and eligibility are preserved"}


def preserve_generation_structure(frame: pd.DataFrame) -> pd.DataFrame:
    """Freeze the generation-stage reference before predictor columns replace it."""
    frame = frame.copy()
    for name, source in (("design_structure_path", "cif_path"),
                         ("design_target_chain", "target_chain"),
                         ("design_binder_chain", "binder_chain"),
                         ("design_reference_stage", "structure_stage")):
        if name not in frame and source in frame:
            frame[name] = frame[source]
    return frame


def annotate_frame(frame: pd.DataFrame, cfg: dict, target: dict, run_dir: Path,
                   id_column="design_id", sequence_column="sequence"):
    from .pose_assessment import assess_models
    from .platform.results import ResultReader
    reader = ResultReader(Path(run_dir))
    root = Path(run_dir).resolve()
    inputs, paths = [], {}
    for raw in native(frame.to_dict("records")):
        identity = raw.get(id_column)
        # Missing/blank IDs remain invalid. Stringifying None manufactures an
        # apparently valid identity; lookup by ID also aliases duplicate rows.
        row = {**raw, "model_id": identity,
               "candidate_id": raw.get("source_sequence_id") or identity,
               "sequence": raw.get(sequence_column), "target_sequence": target.get("sequence"),
               "design_expected": cfg.get("run", {}).get("mode") == "design"}
        # _source also accepts cif_path for standalone callers. Do not leave an
        # unchecked raw fallback after containment rejected structure_path.
        row.pop("cif_path", None)
        # Reader applies run containment and supports portable archived paths.
        for key, source in (("structure_path", "cif_path"),
                            ("design_structure_path", "design_structure_path")):
            value = raw.get(source)
            if isinstance(value, str) and value:
                try:
                    resolved = reader.artifact_path(value)
                    row[key] = str(resolved)
                    portable = resolved.relative_to(root).as_posix()
                    receipt_key = "design_structure_receipt_path" if key.startswith("design_") else "structure_receipt_path"
                    row[receipt_key] = portable
                    paths[str(resolved)] = portable
                    if Path(value).is_absolute():
                        paths[value] = portable
                except (OSError, ValueError):
                    row[key] = None
            else:
                row[key] = None
        inputs.append(row)
    try:
        result = assess_models(inputs, cfg)
    except _INTERRUPTIONS:
        raise
    except Exception as exc:
        result = _unassessed(inputs, cfg, exc)
    result = _portable_details(result, paths)
    output = frame.copy()
    # Keep every original value, dtype and index. Only diagnostic columns are
    # attached by input position; duplicated/invalid IDs cannot overwrite rows.
    excluded = {"model_id", "input_index", "candidate_id", "source_sequence_id", "case_id",
                "sequence", "target_sequence", "predictor_engine", "engine"}
    if result["models"]:
        columns = {key for annotation in result["models"] for key in annotation if key not in excluded}
        for key in sorted(columns):
            values = [annotation.get(key) for annotation in result["models"]]
            if key == "pose_assessment":
                values = [json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) for value in values]
            output[key] = pd.Series(values, index=frame.index, dtype=object)
    return output, result


def assess_benchmark_payload(payload: dict, *, cfg=None, design_coordinates=None) -> dict:
    """Add diagnostics to an immutable saved report; never rerun a predictor.

    ``design_coordinates`` is keyed by candidate_record_id. Each asset supplies
    content, format, target_chain, binder_chain and its actual reference stage.
    Missing generation coordinates remain explicitly unassessed.
    """
    from .pose_assessment import POLICY_VERSION, assess_models
    result = copy.deepcopy(payload)
    targets = {row["pdb_id"]: row["sequence"] for row in result["targets"]}
    generated = {row["candidate_record_id"]: row for row in result["generated_candidates"]}
    assets = design_coordinates or result.get("design_coordinates", {})
    inputs = []
    for raw in result["models"]:
        candidate_key = raw["case_id"] + "__" + raw["source_sequence_id"]
        row = {**raw, "model_id": raw["id"], "candidate_id": raw["source_sequence_id"],
               "target_sequence": targets[raw["pdb_id"]], "design_expected": True}
        coordinate = result.get("coordinates", {}).get(raw.get("structure_key"))
        # Never read an archived absolute server path from a portable report.
        row.pop("cif_path", None)
        row.pop("design_structure_path", None)
        if coordinate:
            row.update(structure_content=coordinate["content"], structure_format=coordinate["format"])
        asset = assets.get(candidate_key)
        if asset:
            content = asset["content"]
            digest = hashlib.sha256(content.encode()).hexdigest()
            if asset.get("sha256") is not None and asset["sha256"] != digest:
                raise ValueError("Generation-stage coordinate hash differs from its receipt")
            candidate = generated.get(candidate_key, {})
            row.update(design_structure_content=content, design_structure_format=asset["format"],
                       design_target_chain=asset.get("target_chain", candidate.get("target_chain")),
                       design_binder_chain=asset.get("binder_chain", candidate.get("binder_chain")),
                       design_reference_stage=asset.get("reference_stage", candidate.get("structure_stage")))
        else:
            row.pop("design_structure_content", None)
        inputs.append(row)
    case_configs = result.get("pose_configs", {}) if cfg is None else {}
    if case_configs and inputs:
        parts = [assess_models([row for row in inputs if row["case_id"] == case], case_configs.get(case))
                 for case in sorted({row["case_id"] for row in inputs})]
        policies = {case: part["policy"] for case, part in zip(sorted({row["case_id"] for row in inputs}), parts)}
        unique_policies = {policy["sha256"] for policy in policies.values()}
        policy = parts[0]["policy"] if len(unique_policies) == 1 else {
            "version": POLICY_VERSION, "calibrated": False, "scope": "per_case", "case_policies": policies}
        assessment = {**parts[0], "policy": policy,
                      **{key: [row for part in parts for row in part[key]] for key in ("models", "candidates", "pairs")}}
    else:
        assessment = assess_models(inputs, cfg)
    annotations = {row["model_id"]: row for row in assessment["models"]}
    for row in result["models"]:
        row.update({k: v for k, v in annotations[row["id"]].items()
                    if k not in {"model_id", "candidate_id", "source_sequence_id", "case_id",
                                 "sequence", "target_sequence", "predictor_engine", "engine"}})
    result["pose_assessment"] = assessment
    result["design_coordinates"] = copy.deepcopy(assets)
    result.setdefault("interpretation", {}).update(
        pose_policy_version=assessment["policy"].get("version"),
        pose_policy_validation="exploratory_not_calibrated_to_experimental_binding",
        recorded_prediction_scores_recomputed=False,
        native_reference_used_for_pose_selection=False,
        new_inference_from_report=0)
    return result
