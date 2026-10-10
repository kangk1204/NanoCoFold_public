from __future__ import annotations

import re
from difflib import SequenceMatcher

import numpy as np
import pandas as pd

import warnings as _warnings

from .config import cfg_get

_AA_PATTERN = "ACDEFGHIKLMNPQRSTVWY"
_CDR3_PATTERN = re.compile(rf"C([{_AA_PATTERN}]{{5,30}}?)WG[{_AA_PATTERN}]{{1,2}}G")
_ANCHORED_CDR3_PATTERN = re.compile(rf"[YF]{{2}}C([{_AA_PATTERN}]{{5,}}?)WG[{_AA_PATTERN}]{{1,2}}G")
_LONG_CDR3_PATTERN = re.compile(rf"C([{_AA_PATTERN}]{{5,}}?)WG[{_AA_PATTERN}]{{1,2}}G")


def guess_cdr3(sequence: str, fallback_length: int = 15) -> str:
    """VHH CDR3 근사 추출: C...WGxG 모티프 기반. ANARCI 없이 클러스터링에 사용."""
    # Non-overlapping matches keep an internal CDR3 cysteine inside the loop.
    # The usual FR3 YYC/YFC anchor also handles insertions beyond 30 residues.
    for pattern in (_ANCHORED_CDR3_PATTERN, _CDR3_PATTERN, _LONG_CDR3_PATTERN):
        matches = list(pattern.finditer(sequence))
        if matches:
            return matches[-1].group(1)
    return sequence[-fallback_length:]


def cdr3_identity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def cluster_cdr3(frame: pd.DataFrame, identity: float = 0.85, sequence_column: str = "sequence", cdr_ranges: dict | None = None) -> pd.Series:
    if (isinstance(identity, (bool, np.bool_)) or not isinstance(identity, (int, float, np.number))
            or not np.isfinite(identity) or not 0 < identity <= 1):
        raise ValueError("CDR3 cluster_identity는 0보다 크고 1 이하의 비율이어야 합니다.")
    sequences = []
    for _, row in frame.iterrows():
        explicit_cdr3 = row.get("cdr3_sequence")
        if explicit_cdr3 is not None and not pd.isna(explicit_cdr3):
            if (not isinstance(explicit_cdr3, str) or not explicit_cdr3
                    or any(aa not in _AA_PATTERN for aa in explicit_cdr3)
                    or explicit_cdr3 not in str(row[sequence_column])):
                raise ValueError("cdr3_sequence must be a nonempty amino-acid substring of the candidate sequence")
            sequences.append(explicit_cdr3)
        elif cdr_ranges and "CDR3" in cdr_ranges:
            start, end = cdr_ranges["CDR3"]
            sequences.append(str(row[sequence_column])[start - 1 : end])
        else:
            sequences.append(guess_cdr3(str(row[sequence_column])))
    clusters: list[str] = []
    representatives: list[str] = []
    cached: dict[str, str] = {}
    for cdr3 in sequences:
        if cdr3 in cached:
            clusters.append(cached[cdr3])
            continue
        assigned = None
        for index, representative in enumerate(representatives):
            matcher = SequenceMatcher(None, cdr3, representative)
            # These are rigorous upper bounds on ratio, not approximations.
            if (matcher.real_quick_ratio() >= identity and matcher.quick_ratio() >= identity
                    and matcher.ratio() >= identity):
                assigned = f"cluster_{index + 1:02d}"
                break
        if assigned is None:
            representatives.append(cdr3)
            assigned = f"cluster_{len(representatives):02d}"
        clusters.append(assigned)
        cached[cdr3] = assigned
    return pd.Series(clusters, index=frame.index, name="cluster_id")


def _zscore(series: pd.Series, higher_is_better: bool = True) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if values.notna().sum() < 2:
        return pd.Series(0.0, index=series.index)
    # Positive rescaling preserves z-scores while preventing finite unit
    # magnitudes from overflowing the mean/variance or underflowing variance.
    scale = values.abs().max()
    if scale > 0:
        values = values / scale
    filled = values.fillna(values.median())
    std = filled.std(ddof=0)
    if std == 0 or np.isnan(std):
        return pd.Series(0.0, index=series.index)
    z = (filled - filled.mean()) / std
    return z if higher_is_better else -z


def composite_score(frame: pd.DataFrame, cfg: dict) -> pd.Series:
    weights = cfg_get(cfg, "rank.weights", {}) or {}
    score = pd.Series(0.0, index=frame.index)
    total = 0.0
    scale = max((abs(float(weight)) for metric, weight in weights.items()
                 if metric in frame.columns), default=0.0)
    for metric, weight in weights.items():
        if metric not in frame.columns:
            _warnings.warn(
                f"rank.weights 의 '{metric}' 컬럼이 결과에 없어 가중치가 무시됩니다.",
                RuntimeWarning,
                stacklevel=2,
            )
            continue
        higher_is_better = weight >= 0
        magnitude = abs(float(weight)) / scale if scale else 0.0
        score += _zscore(frame[metric], higher_is_better) * magnitude
        total += magnitude
    if total > 0:
        score = score / total
    return score.round(4)


def ranking_policy(cfg: dict, frame: pd.DataFrame | None = None) -> dict:
    """Describe the configured relative ordering; this is not an affinity model."""
    weights = cfg_get(cfg, "rank.weights", {}) or {}
    engines = []
    if frame is not None:
        column = next((key for key in ("predictor_engine", "engine") if key in frame), None)
        if column:
            engines = sorted({str(value) for value in frame[column].dropna() if str(value).strip()})
    mixed = len(engines) > 1 and not cfg_get(cfg, "screen.comparison.enabled", False)
    return {
        "method": "mixed_engine_batch_weighted_zscore_legacy" if mixed else "within_engine_batch_weighted_zscore_v1",
        "recorded_prediction_engines": engines,
        "weights": weights,
        "normalization": "batch z-score; missing finite values filled with batch median; fewer than two values or constant columns contribute zero",
        "normalization_population": ("recorded mixed prediction-engine input, including controls; legacy partial-rescore path"
                                     if mixed else "recorded ranking input in the same prediction engine, including controls"),
        "failed_row_handling": "failed/interrupted prediction rows are preserved with unavailable scores/ranks, excluded before normalization and never selected even under fallback",
        "selection": "filter recorded eligibility and controls, then apply diversity limits without renormalizing",
        "cross_engine_scores_combined": mixed,
        "calibrated_binding_probability": False,
        "affinity_improvement_established": False,
        "sequence_priors": {key: weight for key, weight in weights.items() if key in {"esm_score", "antifold"}},
        "sequence_prior_interpretation": "sequence/folding plausibility only; no measured affinity labels or binding calibration",
        "developability_gate": "dev_severe: five severe risk flags",
        "developability_weight": "dev_flags: all evaluated True risk flags; not the severe-only count",
    }


def rank_candidates(frame: pd.DataFrame, cfg: dict, cdr_ranges: dict | None = None, sequence_column: str = "sequence") -> pd.DataFrame:
    ranked = frame.copy().reset_index(drop=True)
    if "cluster_id" not in ranked.columns:
        ranked["cluster_id"] = cluster_cdr3(
            ranked,
            identity=float(cfg_get(cfg, "rank.cdr3.cluster_identity", 0.85)),
            sequence_column=sequence_column,
            cdr_ranges=cdr_ranges,
        )
    failed = _failed_prediction_rows(ranked)
    ranked["composite_score"] = composite_score(ranked.loc[~failed], cfg).reindex(ranked.index)
    ranked = ranked.sort_values("composite_score", ascending=False, kind="stable").reset_index(drop=True)
    ranked["rank"] = ranked.index + 1
    failed = _failed_prediction_rows(ranked)
    ranked["rank"] = ranked["rank"].astype("Int64").mask(failed)
    return ranked


def _failed_prediction_rows(frame: pd.DataFrame) -> pd.Series:
    failed = pd.Series(False, index=frame.index)
    if "error" in frame:
        failed |= frame["error"].notna() & frame["error"].astype(str).str.strip().ne("")
    for column in ("prediction_status", "status"):
        if column in frame:
            failed |= frame[column].astype(str).str.lower().isin({"failed", "error", "interrupted", "cancelled", "canceled"})
    return failed


def select_final(
    frame: pd.DataFrame,
    cfg: dict,
    cdr_ranges: dict | None = None,
    sequence_column: str = "sequence",
    eligible_column: str = "final_eligible",
    allow_fallback: bool | None = None,
    pre_ranked: bool = False,
) -> pd.DataFrame:
    if pre_ranked:
        if not {"rank", "composite_score", "cluster_id"} <= set(frame.columns):
            raise ValueError("pre_ranked selection requires rank, composite_score and cluster_id")
        # Removing WT/control rows must not change the normalization cohort or
        # the recorded scores/ranks. Keep the stable order supplied by ranking.
        ranked = frame.copy()
        if not ranked.index.is_unique:
            ranked = ranked.reset_index(drop=True)
    else:
        ranked = rank_candidates(frame, cfg, cdr_ranges, sequence_column)
    ranked = ranked.loc[~_failed_prediction_rows(ranked)].copy()
    fallback = bool(cfg_get(cfg, "rank.fallback_to_ineligible", False)) if allow_fallback is None else allow_fallback
    if eligible_column in ranked.columns:
        filtered = ranked[ranked[eligible_column].fillna(False)]
        if filtered.empty and not fallback:
            empty = ranked.iloc[0:0].copy()
            empty["selection_rank"] = []
            empty["plate_id"] = []
            empty["plate_position"] = []
            return empty
        pool = filtered if not filtered.empty else ranked
    else:
        pool = ranked

    diversity_pool = max(1, int(cfg_get(cfg, "rank.diversity_pool", 200)))
    per_cluster = max(1, int(cfg_get(cfg, "rank.cdr3.per_cluster", 1)))
    final_n = int(cfg_get(cfg, "rank.final_n", 30))
    if final_n <= 0:
        empty = ranked.iloc[0:0].copy()
        empty["selection_rank"] = []
        empty["plate_id"] = []
        empty["plate_position"] = []
        return empty
    pool = pool.head(diversity_pool)

    selected_indices: list[int] = []
    counts: dict[str, int] = {}
    for index, row in pool.iterrows():
        cluster = str(row.get("cluster_id", "cluster_00"))
        if counts.get(cluster, 0) >= per_cluster:
            continue
        selected_indices.append(index)
        counts[cluster] = counts.get(cluster, 0) + 1
        if len(selected_indices) >= final_n:
            break

    if not selected_indices:
        empty = ranked.iloc[0:0].copy()
        empty["selection_rank"] = []
        empty["plate_id"] = []
        empty["plate_position"] = []
        return empty
    selected = ranked.loc[selected_indices].copy()
    selected["selection_rank"] = range(1, len(selected) + 1)
    selected["plate_id"] = [f"plate_{index // 96 + 1:02d}" for index in range(len(selected))]
    selected["plate_position"] = [plate_position(index % 96) for index in range(len(selected))]
    return selected


def plate_position(index: int) -> str:
    """96-well(A-H × 1-12) 위치. 96 초과 시 명확한 오류를 낸다 (조용한 IndexError 방지)."""
    if index < 0 or index >= 96:
        raise ValueError(
            f"plate_position({index}): 96-well 용량을 초과했습니다. rank.final_n 을 96 이하로 줄이세요."
        )
    rows = "ABCDEFGH"
    return f"{rows[index // 12]}{index % 12 + 1}"
