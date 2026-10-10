"""Explain recorded hotspot checks separately from prediction confidence.

Presentation only: never changes a score, exclusion reason or eligibility gate.
Missing assessments are not zero contacts or evidence of site agreement.
"""
from __future__ import annotations

import math

MAX_SAFE_COUNT = 2**53 - 1


def _count(value):
    # Bound before converting: JSON integers can exceed float conversion limits.
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= MAX_SAFE_COUNT and math.isfinite(value) and int(value) == value:
        return int(value)
    return None


def hotspot_assessment(row: dict) -> dict:
    requested, contacted = (_count(row.get(key)) for key in ("n_hotspots_requested", "n_hotspots_contacted"))
    fraction = row.get("hotspot_contact_frac")
    valid_fraction = isinstance(fraction, (int, float)) and not isinstance(fraction, bool) and 0 <= fraction <= 1 and math.isfinite(fraction)
    checks = row.get("threshold_checks")
    check = next((c for c in checks if isinstance(c, dict) and c.get("metric") == "hotspot_contact_frac"), {}) if isinstance(checks, list) else {}
    recorded_pass, check_pass = row.get("pass_hotspot"), check.get("passed")
    passed = recorded_pass if type(recorded_pass) is bool else check_pass
    threshold = check.get("threshold")
    contradictory = (type(recorded_pass) is bool and type(check_pass) is bool and recorded_pass != check_pass)
    if requested is not None and contacted is not None:
        contradictory = contradictory or contacted > requested
        if requested > 0 and valid_fraction:
            # The native scorer records fractions rounded to three decimals.
            contradictory = contradictory or abs(fraction - contacted / requested) > .0005 + 1e-12
    if row.get("hotspot_mapping_failed") is True:
        status, label = "mapping_failed", "hotspot 번호 대응 실패"
    elif contradictory:
        status, label = "not_assessed", "hotspot 기록 불일치"
    elif requested == 0:
        status, label = "not_requested", "hotspot 미지정"
    elif requested is None or contacted is None or contacted > requested or not valid_fraction:
        status, label = "not_assessed", "hotspot 미평가"
    elif contacted == 0:
        status, label = "no_contact", "지정 hotspot 미접촉"
    elif passed is True:
        status, label = "threshold_met", "hotspot 접촉 기준 충족"
    elif passed is False:
        status, label = "threshold_unmet", "hotspot 접촉 기준 미달"
    else:
        status, label = "threshold_unrecorded", "hotspot 판정 미기록"
    return {"status": status, "label": label, "requested": requested, "contacted": contacted,
            "contact_fraction": fraction if valid_fraction and status not in {"mapping_failed", "not_assessed", "not_requested"} else None,
            "threshold": threshold, "passed": passed if type(passed) is bool and status not in {"mapping_failed", "not_assessed", "not_requested"} else None}


def hotspot_filter_fraction(row: dict):
    """Read-only postfilter value, retaining ratio-only legacy archive support."""
    if row.get("hotspot_mapping_failed"):
        return None
    assessment = hotspot_assessment(row)
    if assessment["contact_fraction"] is not None:
        return assessment["contact_fraction"]
    if "n_hotspots_requested" not in row and "n_hotspots_contacted" not in row:
        checks = row.get("threshold_checks")
        check = next((c for c in checks if isinstance(c, dict) and c.get("metric") == "hotspot_contact_frac"), {}) if isinstance(checks, list) else {}
        recorded_pass, check_pass = row.get("pass_hotspot"), check.get("passed")
        if type(recorded_pass) is bool and type(check_pass) is bool and recorded_pass != check_pass:
            return None
        fraction = row.get("hotspot_contact_frac")
        if isinstance(fraction, (int, float)) and not isinstance(fraction, bool) and 0 <= fraction <= 1 and math.isfinite(fraction):
            return fraction
    return None
