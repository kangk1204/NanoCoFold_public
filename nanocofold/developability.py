from __future__ import annotations

from pathlib import Path
import json
import math

import numpy as np
import pandas as pd

from .config import cfg_get, cfg_seed, resolve_path, developability_patterns
from .utils import seq as seq_utils, shell
from .utils.atomic import write_csv

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"
ASSESSMENT_POLICY = "sequence_liability_screen_v2"

SEVERE_RULES = {
    "free_cys",
    "glycosylation",
    "deamidation_cdr",
    "isomerization_cdr",
    "cleavage_cdr",
    "hydrophobic_patch_cdr",
}


def _chemical_hits(hits: list[dict]) -> list[dict]:
    """Classify the matched residue, including legacy/custom motif settings.

    A configured pattern is a screen, not evidence that a reaction occurred.
    Legacy defaults put DG/DS under deamidation and DP under isomerization.
    Classify actual matches and count each site/reaction once. Keep the input
    labels as provenance; do not edit the user's configuration.
    """
    unique: dict[tuple, dict] = {}
    for raw in hits:
        hit = dict(raw)
        source = hit["type"]
        motif = hit["motif"]
        if source in {"deamidation", "isomerization", "cleavage"}:
            if motif.startswith("N"):
                hit["type"] = "deamidation"
            elif motif.startswith("D"):
                hit["type"] = "cleavage" if motif.startswith("DP") else "isomerization"
            # Non-N/D custom matches retain their configured screen label;
            # no reaction is inferred for them.
            hit["chemistry_assigned"] = motif.startswith(("N", "D"))
        key = (hit["type"], hit["pos"])
        if key not in unique:
            unique[key] = {**hit, "configured_types": [], "matched_motifs": []}
        for field, value in (("configured_types", source), ("matched_motifs", motif)):
            if value not in unique[key][field]:
                unique[key][field].append(value)
    return list(unique.values())


def gate_reason(row, maximum: float) -> str | None:
    """Read a versioned count gate without reinterpreting historical rows."""
    raw = row.get("dev_severe")
    try:
        count = float(raw)
    except (TypeError, ValueError):
        return "developability"
    if isinstance(raw, (bool, np.bool_)) or not math.isfinite(count) or count < 0 or not count.is_integer():
        return "developability"
    if str(row.get("dev_assessment_policy")) == ASSESSMENT_POLICY and str(row.get("dev_assessment_status")) == "review_required":
        return "developability_review_required"
    if count > maximum:
        return ("developability_review_required" if str(row.get("dev_assessment_policy")) == ASSESSMENT_POLICY
                else "developability")
    return None


def selection_held(row) -> bool:
    """A v2 review/unassessed state cannot be bypassed by rank fallback."""
    if str(row.get("dev_assessment_policy")) != ASSESSMENT_POLICY:
        return False
    if str(row.get("dev_assessment_status")) != "screen_pass":
        return True
    try:
        maximum = float(row.get("dev_screen_limit"))
    except (TypeError, ValueError):
        return True
    if isinstance(row.get("dev_screen_limit"), (bool, np.bool_)) or not math.isfinite(maximum) or maximum < 0:
        return True
    evaluated = row.get("dev_cdr_evaluated")
    if isinstance(evaluated, str):
        evaluated = evaluated.strip().lower() == "true"
    elif isinstance(evaluated, (bool, np.bool_, int, float, np.integer, np.floating)):
        evaluated = math.isfinite(float(evaluated)) and evaluated == 1
    else:
        evaluated = False
    return gate_reason(row, maximum) is not None or not evaluated


def review_only_hold(row) -> bool:
    """Presentation only: distinguish a sole review hold from other failures."""
    reasons = row.get("exclusion_reasons")
    if not isinstance(reasons, list):
        reasons = str(row.get("exclusion_reason", "")).split(";")
    return (str(row.get("dev_assessment_policy")) == ASSESSMENT_POLICY
            and str(row.get("dev_assessment_status")) == "review_required"
            and set(filter(None, reasons)) == {"developability_review_required"})


def sequence_liabilities(sequence: str, cfg: dict, cdr_ranges: dict[str, tuple[int, int]] | None = None) -> dict:
    sequence = seq_utils.clean_sequence(sequence)
    if not sequence:
        raise ValueError("개발가능성 평가에는 비어 있지 않은 표준 20개 아미노산 서열이 필요합니다.")
    dev = cfg_get(cfg, "developability", {}) or {}
    required_cdrs = {"CDR1", "CDR2", "CDR3"}
    normalized_ranges: dict[str, tuple[int, int]] = {}
    for name, bounds in (cdr_ranges or {}).items():
        if name not in required_cdrs or not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            continue
        try:
            start, end = int(bounds[0]), int(bounds[1])
        except (TypeError, ValueError):
            continue
        if 1 <= start <= end <= len(sequence):
            normalized_ranges[name] = (start, end)
    valid_cdrs = set(normalized_ranges)
    cdr_evaluated = valid_cdrs == required_cdrs
    cdr_positions: set[int] = set()
    for start, end in normalized_ranges.values():
        cdr_positions.update(range(start, end + 1))

    motif_patterns = developability_patterns(cfg)
    hits = _chemical_hits(seq_utils.find_motif_hits(sequence, motif_patterns))
    for hit in hits:
        hit["cdr_regions"] = [name for name, (start, end) in normalized_ranges.items() if start <= hit["pos"] <= end]
    in_cdr = {name: [hit for hit in hits if hit["type"] == name and hit["pos"] in cdr_positions] for name in motif_patterns}

    cys_count = sequence.count("C")
    max_cys = int(dev.get("max_cys", 2))
    patches = seq_utils.hydrophobic_patches(
        sequence,
        window=int(dev.get("hydrophobic_window", 5)),
        threshold=float(dev.get("hydrophobic_mean_kd", 1.6)),
    )
    cdr_patches = [patch for patch in patches if any(pos in cdr_positions for pos in range(patch[0], patch[1] + 1))] if cdr_positions else []

    pi = seq_utils.isoelectric_point(sequence)
    charge = seq_utils.net_charge(sequence)
    cdr_flags = {
        "deamidation_cdr": bool(in_cdr.get("deamidation")) if cdr_evaluated else None,
        "isomerization_cdr": bool(in_cdr.get("isomerization")) if cdr_evaluated else None,
        "cleavage_cdr": bool(in_cdr.get("cleavage")) if cdr_evaluated else None,
        "oxidation_cdr": bool(in_cdr.get("oxidation")) if cdr_evaluated else None,
        "hydrophobic_patch_cdr": bool(cdr_patches) if cdr_evaluated else None,
    }
    flags = {
        "free_cys": cys_count > max_cys or cys_count % 2 != 0,
        "glycosylation": bool(hits and any(hit["type"] == "glycosylation" for hit in hits)),
        **cdr_flags,
        "pi_out_of_range": not (float(dev.get("pI_min", 5.0)) <= pi <= float(dev.get("pI_max", 9.5))),
        "charge_out_of_range": not (float(dev.get("net_charge_min", -6.0)) <= charge <= float(dev.get("net_charge_max", 6.0))),
    }
    warnings = [name for name, value in flags.items() if value is True and name in SEVERE_RULES]
    unevaluated = [name for name, value in flags.items() if value is None]
    limit = float(cfg_get(cfg, "rank.max_dev_severe", 1))
    status = "unassessed" if not cdr_evaluated else "review_required" if len(warnings) > limit else "screen_pass"
    window_evidence = [{"start": start, "end": end, "max_mean_kd": mean,
                        "cdr_overlap": [name for name, (a, b) in normalized_ranges.items() if start <= b and end >= a]}
                       for start, end, mean in patches]
    return {
        "dev_assessment_policy": ASSESSMENT_POLICY,
        "dev_assessment_status": status,
        "dev_evidence_level": "sequence_screen",
        "dev_screen_limit": limit,
        "dev_review_required": status == "review_required",
        "dev_motif_evidence": json.dumps(hits, ensure_ascii=False, separators=(",", ":")),
        "dev_hydrophobic_evidence": json.dumps(window_evidence, separators=(",", ":")),
        "dev_cdr_evaluated": cdr_evaluated,
        "dev_cdr_missing_regions": ";".join(sorted(required_cdrs - valid_cdrs)),
        "dev_severe_unevaluated": sum(name in SEVERE_RULES for name in unevaluated),
        "dev_unevaluated": ";".join(unevaluated),
        "dev_flags": int(sum(1 for value in flags.values() if value is True)),
        "dev_severe": int(len(warnings)),
        "dev_warnings": ";".join(warnings),
        "n_cys": cys_count,
        "pI": pi,
        "net_charge": charge,
        "mw_kda": seq_utils.molecular_weight(sequence),
        "gravy": seq_utils.gravy(sequence),
        "n_hydrophobic_patches": len(patches),
        "cdr_hydrophobic_patches": len(cdr_patches),
        **{f"dev_{name}": value for name, value in flags.items()},
    }


def add_liabilities(
    frame: pd.DataFrame,
    cfg: dict,
    cdr_ranges: dict[str, tuple[int, int]] | None = None,
    sequence_column: str = "sequence",
    cdr_by_id: dict[str, dict] | None = None,
    id_column: str = "design_id",
) -> pd.DataFrame:
    rows = []
    for _, row in frame.iterrows():
        ranges = cdr_ranges
        if cdr_by_id:
            ranges = cdr_by_id.get(str(row.get(id_column, ""))) or None
        sequence = str(row[sequence_column])
        validated, annotation_error = None, None
        if ranges:
            from .mature import validate_cdr_ranges
            try:
                validated = validate_cdr_ranges(ranges, len(sequence))
            except ValueError as exc:
                # A shared screening range may fit only some library members.
                # Keep their sequence metrics; invalid CDR metrics stay unavailable.
                annotation_error = str(exc)
        values = sequence_liabilities(sequence, cfg, validated)
        values["cdr3_sequence"] = None
        values["cdr3_annotation_source"] = "invalid_sequence_ranges" if annotation_error else "not_evaluated"
        if validated and "CDR3" in validated:
            start, end = validated["CDR3"]
            values["cdr3_sequence"] = sequence[start - 1 : end]
            values["cdr3_annotation_source"] = "per_candidate_ranges" if cdr_by_id else "shared_sequence_ranges"
        rows.append(values)
    source = frame.drop(columns=[key for key in ("cdr3_sequence", "cdr3_annotation_source") if key in frame])
    return pd.concat([source.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def run_esm_sequence_scores(
    sequences: dict[str, str],
    reference_sequence: str,
    cfg: dict,
    out_dir: Path,
    mock: bool = False,
    dry_run: bool = False,
) -> pd.DataFrame:
    """설계 후보 전체 서열의 ESM-2 pseudo-log-likelihood 및 기준 서열 대비 Δ."""
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame({"design_id": list(sequences.keys()), "sequence": list(sequences.values())})
    input_csv = out_dir / "sequences_in.csv"
    output_csv = out_dir / "sequences_esm.csv"
    write_csv(input_csv, frame)

    if mock or dry_run:
        rng = np.random.default_rng(cfg_seed(cfg, "run.seed") + 7)
        frame["esm_mean_logprob"] = rng.normal(-1.0, 0.15, len(frame)).round(4)
        frame["esm_delta"] = (frame["esm_mean_logprob"] - frame["esm_mean_logprob"].max()).round(4)
        return frame

    esm_cfg = cfg_get(cfg, "mature.esm", {}) or {}
    env = resolve_path(cfg, esm_cfg.get("env"))
    argv = [
        str(shell.env_python(env)), str(SCRIPTS_DIR / "esm_llr.py"),
        "--sequences", str(input_csv),
        "--reference-sequence", reference_sequence,
        "--output", str(output_csv),
        "--model", str(esm_cfg.get("model", "esm2_t33_650M_UR50D")),
    ]
    shell.run_in_env(env, argv, log_path=out_dir / "esm_sequences.log", dry_run=dry_run)
    if output_csv.exists():
        return pd.read_csv(output_csv)
    frame["esm_mean_logprob"] = float("nan")
    frame["esm_delta"] = float("nan")
    return frame


def triage(
    frame: pd.DataFrame,
    cfg: dict,
    cdr_ranges: dict[str, tuple[int, int]] | None = None,
    sequence_column: str = "sequence",
    cdr_by_id: dict[str, dict] | None = None,
    id_column: str = "design_id",
) -> pd.DataFrame:
    return add_liabilities(frame, cfg, cdr_ranges, sequence_column, cdr_by_id=cdr_by_id, id_column=id_column)
