from __future__ import annotations

import re
import hashlib
import json
from collections import Counter

import numpy as np
import pandas as pd

import warnings as _warnings

from .config import cfg_get

_AA_PATTERN = "ACDEFGHIKLMNPQRSTVWY"
_CDR3_PATTERN = re.compile(rf"C([{_AA_PATTERN}]{{5,30}}?)WG[{_AA_PATTERN}]{{1,2}}G")
_ANCHORED_CDR3_PATTERN = re.compile(rf"[YF]{{2}}C([{_AA_PATTERN}]{{5,60}}?)WG[{_AA_PATTERN}]{{1,2}}G")
_LONG_CDR3_PATTERN = re.compile(rf"C([{_AA_PATTERN}]{{5,60}}?)WG[{_AA_PATTERN}]{{1,2}}G")


def guess_cdr3(sequence: str, fallback_length: int = 15) -> str:
    """VHH CDR3 근사 추출: C...WGxG 모티프 기반. ANARCI 없이 클러스터링에 사용."""
    # Non-overlapping matches keep an internal CDR3 cysteine inside the loop.
    # The usual FR3 YYC/YFC anchor also handles insertions beyond 30 residues.
    # These motif-only guesses stop at 60 residues to avoid spanning FR2/FR3.
    # Explicit, validated cdr3_sequence/ranges remain unrestricted by this
    # heuristic bound and take precedence in cluster_cdr3.
    for pattern in (_ANCHORED_CDR3_PATTERN, _CDR3_PATTERN, _LONG_CDR3_PATTERN):
        matches = list(pattern.finditer(sequence))
        if matches:
            return matches[-1].group(1)
    return sequence[-fallback_length:]


def cdr3_identity(a: str, b: str) -> float:
    """Symmetric global edit similarity; insertions/deletions count as differences.

    This normalized Levenshtein score is not a calibrated biological distance.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    previous = list(range(len(b) + 1))
    for i, aa in enumerate(a, 1):
        current = [i]
        for j, bb in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (aa != bb)))
        previous = current
    return 1.0 - previous[-1] / max(len(a), len(b))


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
    frequencies = Counter(sequences)
    for cdr3 in sorted(frequencies, key=lambda value: (-frequencies[value], -len(value), value)):
        if cdr3 in cached:
            clusters.append(cached[cdr3])
            continue
        assigned = None
        for index, representative in enumerate(representatives):
            if (min(len(cdr3), len(representative)) / max(1, len(cdr3), len(representative)) >= identity
                    and cdr3_identity(cdr3, representative) >= identity):
                assigned = f"cluster_{index + 1:02d}"
                break
        if assigned is None:
            representatives.append(cdr3)
            assigned = f"cluster_{len(representatives):02d}"
        clusters.append(assigned)
        cached[cdr3] = assigned
    return pd.Series([cached[s] for s in sequences], index=frame.index, name="cluster_id")


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
    if len(recorded_prediction_engines(frame)) > 1:
        raise ValueError("Mixed prediction engines require separate ranking cohorts; use engine_comparison.rank_and_select.")
    weights, ignored = _available_weights(frame, cfg)
    for metric in (key for key, weight in ignored.items() if weight):
        _warnings.warn(
            f"rank.weights 의 '{metric}' 지표가 전체 성공 후보에서 미평가여서 가중치를 제외합니다.",
            RuntimeWarning, stacklevel=2,
        )
    original_index = frame.index
    frame = frame.loc[_normalization_rows(frame, cfg)]
    score = pd.Series(0.0, index=frame.index)
    total = 0.0
    scale = max((abs(float(weight)) for metric, weight in weights.items()
                 if metric in frame.columns), default=0.0)
    for metric, weight in weights.items():
        higher_is_better = weight >= 0
        magnitude = abs(float(weight)) / scale if scale else 0.0
        score += _zscore(frame[metric], higher_is_better) * magnitude
        total += magnitude
    if total > 0:
        score = score / total
    # Missing CDR-dependent liabilities cannot earn a favorable risk score.
    # Preserve partial flag counts but hold the combined ranking until assessed.
    # Formatting belongs to the presentation layer. Rounding before sorting
    # would turn distinct measurements into artificial ID-ordered ties.
    return score.reindex(original_index)


def ranking_policy(cfg: dict, frame: pd.DataFrame | None = None, *, selected: pd.DataFrame | None = None) -> dict:
    """Describe the configured relative ordering; this is not an affinity model."""
    weights = cfg_get(cfg, "rank.weights", {}) or {}
    engines = recorded_prediction_engines(frame) if frame is not None else []
    mixed = len(engines) > 1
    applied, ignored = _available_weights(frame, cfg) if frame is not None else (weights, {})
    policy = {
        "method": "within_engine_batch_weighted_zscore_v1",
        "recorded_prediction_engines": engines,
        "weights": weights,
        "applied_weights": applied,
        "ignored_weights": ignored,
        "cdr3_similarity": "normalized_levenshtein_v2; frequency-descending, length-descending, lexical-ascending greedy representatives",
        "normalization": "batch z-score; no-contact rows and rows missing a nonzero-weighted metric measured in other successful candidates excluded/held; globally absent or entirely unmeasured metrics are recorded as ignored; fewer than two values or constant columns contribute zero",
        "normalization_population": "recorded ranking input in the same prediction engine, including controls; rows with unassessed CDR liabilities excluded when developability is weighted",
        "failed_row_handling": "failed/interrupted prediction rows are preserved with unavailable scores/ranks, excluded before normalization and never selected even under fallback",
        "selection": "filter recorded eligibility and controls, then apply diversity limits without renormalizing",
        "score_precision": "composite_unrounded_float64; input metric precision as recorded; presentation rounding never controls ordering",
        "source_metric_precision": {
            "interface_pae": "stored_rounded_3_decimal_angstrom",
            "interface_pae_directional_median_p90": "stored_rounded_3_decimal_angstrom",
            "other_metrics": "as_recorded_by_metric_producer; not asserted universally unrounded",
        },
        "rank_tie_break": "exact composite_score descending, then design_id or variant_id ascending; stable input order only when identifiers are absent or identical",
        "rank_interpretation": "ordinal deterministic record order; equal composite scores do not establish a scientific difference",
        "hotspot_mapping_gate": "any failed requested-site mapping holds ranking and selection when hotspot_contact_frac has nonzero weight, even when the contact ratio is finite",
        "plate_scope": "engine-namespaced plate IDs whenever ranking is engine-scoped; within-engine wells are not a combined ordering or synthesis authorization",
        "cross_engine_scores_combined": False,
        "calibrated_binding_probability": False,
        "affinity_improvement_established": False,
        "sequence_priors": {key: weight for key, weight in weights.items() if key in {"esm_score", "antifold"}},
        "sequence_prior_interpretation": "sequence/folding plausibility only; no measured affinity labels or binding calibration",
        "developability_gate": "dev_severe: five severe risk flags",
        "developability_weight": "dev_flags: all evaluated True risk flags; incomplete CDR assessment holds composite_score/rank and selection",
        "fallback_scope": "recorded ineligible rows with available composite scores only; failed predictions, unavailable interfaces and unassessed weighted CDR liabilities stay held",
    }
    if frame is None:
        return policy
    if mixed or "ranking_scope" in frame:
        labels = prediction_engine_labels(frame)
        scoped = engine_scopes_recorded(frame)
        groups = {}
        for engine in engines:
            group = frame.loc[labels.eq(engine)].copy()
            if scoped and group["ranking_scope"].isna().all():
                continue
            engine_selected = selected.loc[prediction_engine_labels(selected).eq(engine)] if selected is not None else None
            groups[engine] = ranking_policy(cfg, group.drop(columns="ranking_scope", errors="ignore"), selected=engine_selected)
            groups[engine].update(final_n=int(cfg_get(cfg, "rank.final_n", 30)), final_n_scope="this_prediction_engine_cohort")
        policy.update(
            method="per_engine_batch_weighted_zscore_v2" if scoped else "mixed_engine_ranking_requires_separation_v2",
            calculation_status="engine_scoped" if scoped else "not_applied_to_combined_input",
            per_engine_policies=groups,
            applied_weights={key: value for key, value in weights.items()
                             if groups and all(group["applied_weights"].get(key) == value and key in group["applied_weights"] for group in groups.values())},
            ignored_weights={key: value for key, value in weights.items()
                             if groups and all(group["ignored_weights"].get(key) == value and key in group["ignored_weights"] for group in groups.values())},
            weight_summary_scope="common_to_all_engine_cohorts; per_engine_policies are authoritative",
            combined_ranking_available=False,
            combined_synthesis_order_authorized=False,
            final_n_scope="per_prediction_engine_cohort; total may exceed rank.final_n",
            selected_observations_total=int(len(selected)) if selected is not None else None,
            unrecorded_engine_observations=int(labels.isna().sum()),
            unrecorded_engine_handling="preserved_unranked_unselected; no engine inferred from configuration",
            unrecognized_engine_observations=int(frame.get("ranking_unavailable_reason", pd.Series(None, index=frame.index, dtype=object)).eq("prediction_engine_unrecognized").sum()),
            unrecognized_engine_handling="preserved_unranked_unselected; unsupported engine label is not reassigned",
            normalization_population="separate recorded prediction-engine cohorts; no cross-engine normalization or combined ordering",
        )
        if not cfg_get(cfg, "screen.comparison.enabled", False):
            policy["partial_rescore_limitation"] = "engine assignment depends on first-stage selection; candidate subsets differ and engine performance, affinity and cross-engine ranks cannot be compared"
        return policy
    policy.update(_cohort_policy(frame, cfg, engines[0] if engines else None))
    policy["selected_observation_count"] = int(len(selected)) if selected is not None else None
    return policy


def prediction_engine_labels(frame: pd.DataFrame) -> pd.Series:
    """Use only recorded row provenance, never infer engines from configuration."""
    labels = pd.Series(None, index=frame.index, dtype=object)
    for key in ("predictor_engine", "engine"):
        if key in frame:
            values = frame[key].map(lambda value: value.strip() if isinstance(value, str) and value.strip() else None)
            labels = labels.where(labels.notna(), values)
    return labels


def recorded_prediction_engines(frame: pd.DataFrame) -> list[str]:
    return sorted(set(prediction_engine_labels(frame).dropna()))


def engine_scopes_recorded(frame: pd.DataFrame, *, allow_held: bool = True) -> bool:
    """A saved scope must match explicit provenance; unknown rows stay held."""
    if "ranking_scope" not in frame:
        return False
    from .engine_comparison import ENGINES
    labels = prediction_engine_labels(frame)
    valid = frame["ranking_scope"].eq(labels) & labels.isin([*ENGINES, "mock"])
    if allow_held:
        held = (frame.get("rank", pd.Series(np.nan, index=frame.index)).isna()
                & frame.get("composite_score", pd.Series(np.nan, index=frame.index)).isna())
        valid |= frame["ranking_scope"].isna() & held
    return bool(valid.all())


def _normalization_rows(frame: pd.DataFrame, cfg: dict) -> pd.Series:
    available = ~(_failed_prediction_rows(frame) | _interface_unavailable_rows(frame, cfg)
                  | _weighted_unavailable_rows(frame, cfg))
    if cfg_get(cfg, "rank.weights.dev_flags", 0) and "dev_cdr_evaluated" in frame:
        available &= frame["dev_cdr_evaluated"].map(lambda value: isinstance(value, (bool, np.bool_)) and bool(value))
    return available


def _cohort_policy(frame: pd.DataFrame, cfg: dict, engine: str | None) -> dict:
    from .cascade import control_flags
    failed = _failed_prediction_rows(frame)
    normalized = _normalization_rows(frame, cfg)
    applied, ignored = _available_weights(frame, cfg)
    identifier = next((key for key in ("design_id", "variant_id") if key in frame), None)
    controls = control_flags(frame, identifier) if identifier else pd.Series(False, index=frame.index)
    eligible = frame.get("final_eligible", pd.Series(False, index=frame.index)).map(
        lambda value: isinstance(value, (bool, np.bool_)) and bool(value))
    members = sorted(frame[identifier].astype(str).tolist()) if identifier else []
    normalized_members = sorted(frame.loc[normalized, identifier].astype(str).tolist()) if identifier else []
    cohort_key = json.dumps({"engine": engine, "identifier_column": identifier, "members": members,
                             "observation_count": len(frame)}, ensure_ascii=False, sort_keys=True)
    normalization_key = json.dumps({"engine": engine, "identifier_column": identifier, "members": normalized_members,
                                    "observation_count": int(normalized.sum())}, ensure_ascii=False, sort_keys=True)
    reasons = {}
    for metric in ignored:
        if metric not in frame:
            reasons[metric] = "metric_column_absent"
        else:
            values = pd.to_numeric(frame.loc[~failed, metric], errors="coerce")
            reasons[metric] = ("no_valid_requested_site_mapping" if metric == "hotspot_contact_frac" and np.isfinite(values).any()
                               else "no_finite_measurement_in_successful_prediction_cohort")
    # Ratios are computed after the same max-weight scaling used by the score.
    scale = max((abs(float(value)) for value in applied.values()), default=0.)
    denominator = sum(abs(float(value)) / scale for value in applied.values()) if scale else 0.
    normalized_weights = {key: (float(value) / scale / denominator if denominator else 0.) for key, value in applied.items()}
    metric_counts = {}
    for metric, weight in (cfg_get(cfg, "rank.weights", {}) or {}).items():
        values = pd.to_numeric(frame[metric], errors="coerce") if metric in frame else pd.Series(np.nan, index=frame.index)
        if metric == "hotspot_contact_frac":
            values = values.mask(_hotspot_mapping_unavailable_rows(frame, cfg))
        observations = values.loc[normalized & np.isfinite(values)]
        metric_counts[metric] = {"finite_successful_observations": int((~failed & np.isfinite(values)).sum()),
                                 "finite_normalization_observations": int(len(observations)),
                                 "contribution_status": ("zero_weight" if not weight else "ignored" if metric in ignored
                                                         else "fewer_than_two_values_zero_contribution" if len(observations) < 2
                                                         else "constant_zero_contribution" if observations.nunique() <= 1 else "weighted_zscore")}
    return {"cohort_id": "prediction-engine-cohort:" + hashlib.sha256(cohort_key.encode()).hexdigest(),
            "prediction_engine": engine, "prediction_engine_id": "prediction-engine:" + hashlib.sha256(str(engine).encode()).hexdigest() if engine is not None else None,
            "normalization_cohort_id": "normalization-cohort:" + hashlib.sha256(normalization_key.encode()).hexdigest(),
            "cohort_identifier_semantics": "engine and observation-ID multiset membership; exact metric bytes are sealed by the final table SHA256 receipt",
            "cohort_identifier_column": identifier,
            "cohort_observation_ids": members, "ignored_weight_reasons": reasons,
            "normalization_observation_ids": normalized_members,
            "normalized_weights": normalized_weights,
            "metric_observation_counts": metric_counts,
            "cohort_counts": {"input_observations": int(len(frame)), "prediction_failed": int(failed.sum()),
                              "successful_predictions": int((~failed).sum()), "normalization_observations": int(normalized.sum()),
                              "held_observations": int((~normalized).sum()), "control_observations": int(controls.sum()),
                              "normalization_control_observations": int((controls & normalized).sum()),
                              "quality_eligible_observations": int((eligible & ~controls & ~failed).sum()),
                              "rank_available_eligible_observations": int((eligible & ~controls & normalized).sum())},
            "held_reason_counts": {"prediction_failed": int(failed.sum()),
                                   "interface_metrics_unavailable": int((~failed & _interface_unavailable_rows(frame, cfg)).sum()),
                                   "weighted_metrics_unavailable": int((~failed & _weighted_unavailable_rows(frame, cfg)).sum()),
                                   "weighted_cdr_assessment_unavailable": int((~failed & ~normalized & ~_interface_unavailable_rows(frame, cfg) & ~_weighted_unavailable_rows(frame, cfg)).sum())},
            "held_reason_counts_may_overlap": True,
            "controls_in_normalization": True, "controls_excluded_from_selection": True}


def ranking_warnings(frame: pd.DataFrame, cfg: dict) -> list[str]:
    """Explain held rankings while preserving conservative selection and all saved values."""
    if frame.empty or "composite_score" not in frame:
        return []
    if len(recorded_prediction_engines(frame)) > 1:
        labels = prediction_engine_labels(frame)
        return [f"{engine}: {notice}" for engine in recorded_prediction_engines(frame)
                for notice in ranking_warnings(frame.loc[labels.eq(engine)], cfg)]
    _, ignored = _available_weights(frame, cfg)
    notices = (["전체 성공 후보에서 미평가인 순위 지표: " + ", ".join(ignored)
                + ". 해당 가중치를 제외하고 나머지 지표로 순위를 계산했습니다. 미평가를 0점이나 부적격으로 해석하지 마세요."]
               if any(ignored.values()) else [])
    held = frame["composite_score"].isna() & ~_failed_prediction_rows(frame)
    if not held.any():
        return notices
    cdr_held = (held & frame["dev_cdr_evaluated"].map(
        lambda value: not (isinstance(value, (bool, np.bool_)) and bool(value)))
        if "dev_cdr_evaluated" in frame and cfg_get(cfg, "rank.weights.dev_flags", 0)
        else pd.Series(False, index=frame.index))
    message = f"종합점수·기록 순위 보류 {int(held.sum())}/{len(frame)}개 모델. "
    if cdr_held.any():
        message += (f"이 중 {int(cdr_held.sum())}개는 CDR 개발가능성이 미평가입니다. "
                    "ANARCI 환경 또는 서열에 맞는 CDR 범위를 확인하세요. ")
    interface_held = held & _interface_unavailable_rows(frame, cfg)
    if interface_held.any():
        message += (f"이 중 {int(interface_held.sum())}개는 접촉이 없거나 가중 계면 지표가 미평가입니다. "
                    "성공한 무접촉 예측과 측정된 접촉0개 기록은 보존하고 순위·선정은 보류합니다. ")
    hotspot_held = held & _hotspot_mapping_unavailable_rows(frame, cfg)
    if hotspot_held.any():
        message += (f"이 중 {int(hotspot_held.sum())}개는 요청한 결합 부위의 구조 대응이 불완전하여 "
                    "접촉률을 순위에 사용하지 않고 보류합니다. ")
    weighted_held = held & _weighted_unavailable_rows(frame, cfg) & ~interface_held & ~hotspot_held
    if weighted_held.any():
        applied, _ = _available_weights(frame, cfg)
        missing = [metric for metric, weight in applied.items() if weight and
                   (~np.isfinite(pd.to_numeric(frame.loc[weighted_held, metric], errors="coerce"))).any()]
        message += (f"이 중 {int(weighted_held.sum())}개는 다른 성공 후보에서 평가된 가중 지표 "
                    f"({', '.join(missing)}) 값이 없어 중앙값으로 보충하지 않고 보류합니다. ")
    elif not cdr_held.any() and not interface_held.any() and not hotspot_held.any():
        message += "저장된 종합점수가 없어 순위·선별을 추정하지 않습니다. "
    if cfg_get(cfg, "rank.fallback_to_ineligible", False):
        message += ("fallback_to_ineligible=true는 종합점수가 있는 부적격 후보에만 적용됩니다. "
                    "실패 예측이나 가중 CDR 미평가로 보류된 후보는 포함하지 않습니다.")
    return notices + [message.strip()]


def rank_candidates(frame: pd.DataFrame, cfg: dict, cdr_ranges: dict | None = None, sequence_column: str = "sequence") -> pd.DataFrame:
    if len(recorded_prediction_engines(frame)) > 1:
        raise ValueError("Mixed prediction engines require separate ranking cohorts; use engine_comparison.rank_and_select.")
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
    identifier = next((k for k in ("design_id", "variant_id") if k in ranked), None)
    columns = ["composite_score"] + ([identifier] if identifier else [])
    ranked = ranked.sort_values(columns, ascending=[False] + ([True] if identifier else []), kind="stable").reset_index(drop=True)
    ranked["rank"] = ranked.index + 1
    failed = _failed_prediction_rows(ranked)
    ranked["rank"] = ranked["rank"].astype("Int64").mask(failed | ranked["composite_score"].isna())
    ranked["ranking_status"] = np.where(ranked["rank"].notna(), "ranked", "held")
    ranked["ranking_unavailable_reason"] = np.where(failed, "prediction_failed",
        np.where(_interface_unavailable_rows(ranked, cfg), "interface_metrics_unavailable",
                 np.where(_weighted_unavailable_rows(ranked, cfg), "weighted_metrics_unavailable",
                 np.where(ranked["composite_score"].isna(), "weighted_cdr_assessment_unavailable", None))))
    return ranked


_INTERFACE_RANK_METRICS = {
    "interface_plddt", "interface_pae", "interface_pae_ab", "interface_pae_ba",
    "interface_pae_median", "interface_pae_p90", "pdockq", "pdockq2",
    "pdockq2_target", "pdockq2_binder", "buried_sasa", "buried_sasa_per_contact",
}


def _available_weights(frame: pd.DataFrame, cfg: dict) -> tuple[dict, dict]:
    """Use one cohort definition for normalization, holds and the policy record.

    An optional measurement absent from the entire successful cohort is not an
    individual candidate failure. Never let values on failed predictions make
    that optional measurement appear available.
    """
    measured = frame.loc[~_failed_prediction_rows(frame)]
    applied, ignored = {}, {}
    for metric, weight in (cfg_get(cfg, "rank.weights", {}) or {}).items():
        values = pd.to_numeric(measured[metric], errors="coerce") if metric in measured else None
        if metric == "hotspot_contact_frac" and values is not None:
            # An incomplete mapping is not evidence that a usable site metric
            # exists elsewhere in the successful cohort.
            values = values.mask(_hotspot_mapping_unavailable_rows(measured, cfg))
        target = applied if values is not None and np.isfinite(values).any() else ignored
        target[metric] = weight
    return applied, ignored


def _weighted_unavailable_rows(frame: pd.DataFrame, cfg: dict) -> pd.Series:
    """Missing measurements must not acquire a favorable cohort median score."""
    held = _hotspot_mapping_unavailable_rows(frame, cfg)
    for metric, weight in _available_weights(frame, cfg)[0].items():
        if weight:
            values = pd.to_numeric(frame[metric], errors="coerce")
            held |= ~np.isfinite(values).fillna(False)
    return held


def _hotspot_mapping_unavailable_rows(frame: pd.DataFrame, cfg: dict) -> pd.Series:
    """Honor both current booleans and CSV-reloaded failure receipts."""
    if not cfg_get(cfg, "rank.weights.hotspot_contact_frac", 0) or "hotspot_mapping_failed" not in frame:
        return pd.Series(False, index=frame.index)
    return frame["hotspot_mapping_failed"].map(
        lambda value: (isinstance(value, (bool, np.bool_)) and bool(value))
        or (isinstance(value, str) and value.strip().lower() in {"true", "1"})
        or (isinstance(value, (int, float, np.number)) and not isinstance(value, (bool, np.bool_)) and value == 1)
    )


def _interface_unavailable_rows(frame: pd.DataFrame, cfg: dict) -> pd.Series:
    """Keep measured no-contact predictions, but never median-impute an interface."""
    held = pd.Series(False, index=frame.index)
    if "n_contacts" in frame:
        contacts = pd.to_numeric(frame["n_contacts"], errors="coerce")
        held |= contacts.eq(0).fillna(False) & ~frame["n_contacts"].map(lambda v: isinstance(v, (bool, np.bool_)))
    if "interface_assessment_status" in frame:
        held |= frame["interface_assessment_status"].eq("no_contacts").fillna(False)
    for metric, weight in _available_weights(frame, cfg)[0].items():
        if weight and metric in _INTERFACE_RANK_METRICS and metric in frame:
            values = pd.to_numeric(frame[metric], errors="coerce")
            held |= ~np.isfinite(values).fillna(False)
    return held


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
    if len(recorded_prediction_engines(frame)) > 1:
        raise ValueError("Mixed prediction engines require separate ranking cohorts; use engine_comparison.rank_and_select.")
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
    fallback = bool(cfg_get(cfg, "rank.fallback_to_ineligible", False)) if allow_fallback is None else allow_fallback
    available = (~_failed_prediction_rows(ranked) & ~_interface_unavailable_rows(ranked, cfg) & ~_weighted_unavailable_rows(ranked, cfg)
                 & ranked["composite_score"].notna())
    if fallback and not ranked.empty and not available.any():
        messages = ranking_warnings(ranked, cfg)
        _warnings.warn(messages[0] if messages else
            "fallback_to_ineligible=true여도 실패 예측·종합점수 미평가 후보는 선별하지 않습니다.",
            RuntimeWarning, stacklevel=2)
    ranked = ranked.loc[available].copy()
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
    if cfg_get(cfg, "screen.comparison.enabled", False) or "ranking_scope" in selected:
        engine_column = next((key for key in ("predictor_engine", "engine", "ranking_scope") if key in selected), None)
        if engine_column and not selected.empty:
            engines = selected[engine_column].astype(str)
            if engines.nunique() != 1 or engines.str.strip().eq("").any():
                raise ValueError("Comparison plate allocation requires one recorded prediction engine per selection.")
            # Preserve the original per-engine plate identifier explicitly.
            selected["engine_plate_id"] = selected["plate_id"]
            selected["plate_id"] = engines + "__" + selected["engine_plate_id"]
    return selected


def plate_position(index: int) -> str:
    """96-well(A-H × 1-12) 위치. 96 초과 시 명확한 오류를 낸다 (조용한 IndexError 방지)."""
    if index < 0 or index >= 96:
        raise ValueError(
            f"plate_position({index}): 96-well 용량을 초과했습니다. rank.final_n 을 96 이하로 줄이세요."
        )
    rows = "ABCDEFGH"
    return f"{rows[index // 12]}{index % 12 + 1}"
