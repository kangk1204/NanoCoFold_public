"""Describe stored metric methods without replacing a historical score."""
from __future__ import annotations

import math

PDOCKQ_METHOD = "Bryant2022_author_CB8_CBplDDT_unique_interface_v1"
PDOCKQ2_METHOD = "Zhu2023_author_CA8_CAplDDT_contact_weighted_v1"
LEGACY_PDOCKQ2_METHOD = "Zhu2023_author_CA8_contact_weighted"

SOURCES = {
    "pdockq": "https://gitlab.com/ElofssonLab/FoldDock/-/blob/main/src/pdockq.py",
    "pdockq2": "https://gitlab.com/ElofssonLab/afm-benchmark/-/blob/main/src/pdockq2.py",
}


def metric_provenance(row: dict) -> dict:
    """Expose method evidence; never infer an unrecorded implementation version."""
    result = {}
    for metric, current_method in (("pdockq", PDOCKQ_METHOD), ("pdockq2", PDOCKQ2_METHOD)):
        recorded = row.get(metric + "_method")
        value = row.get(metric)
        assessed = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        entry = {"recorded_method": recorded, "effective_method": None,
                 "status": "not_assessed" if not assessed else "method_not_recorded",
                 "caveat": "", "source": SOURCES[metric], "stored_value_preserved": True}
        entry["assessment_status"] = row.get(metric + "_status") or ("evaluated" if assessed else "not_assessed")
        if recorded == current_method:
            entry.update(effective_method=recorded, status="recorded_author_method")
        elif metric == "pdockq2" and recorded == LEGACY_PDOCKQ2_METHOD:
            entry.update(
                effective_method="Zhu2023_CA8_residue_mean_plDDT_contact_weighted_legacy",
                status="legacy_variant",
                caveat="기존 저장값은 잔기 원자 평균 신뢰도로 계산됐습니다. 저자 코드의 접촉 CA 원자 신뢰도 방식과 다르며, 원본 점수는 유지합니다.")
        elif recorded:
            entry.update(effective_method=recorded, status="unrecognized_recorded_method",
                         caveat="기록된 계산법을 표시합니다. 현재 저자 원자 신뢰도 방식과의 일치는 확인되지 않았습니다.")
        elif assessed:
            entry["caveat"] = "계산법 버전이 기록되지 않은 기존 점수입니다. 저자 원자 신뢰도 방식과의 일치는 확인되지 않았으며, 원본 점수는 유지합니다."
        if entry["assessment_status"] in {"no_interface", "missing_contact_atoms", "missing_plddt", "missing_pae"}:
            detail = {"no_interface": "정의된 접촉 계면 없음", "missing_contact_atoms": "접촉 원자 누락",
                      "missing_plddt": "예측 신뢰도 누락", "missing_pae": "PAE 누락"}[entry["assessment_status"]]
            entry["caveat"] = (entry["caveat"] + " " if entry["caveat"] else "") + detail + ": 미평가로 읽습니다. 원저자 반환값과 계산 상태는 별도로 보존합니다."
        result[metric] = entry
    return result
