"""Fail closed when a reviewed wizard snapshot is changed before inference."""
from __future__ import annotations

import hashlib

from .config import cfg_get, resolve_path
from .hotspots import parse_hotspot_spec


def validate_reviewed_input(cfg: dict, *, design: bool = False) -> None:
    review = cfg_get(cfg, "input_review")
    if not review:
        return
    error = "검토한 입력과 설정이 달라졌습니다. 입력 마법사에서 다시 확인하고 저장하세요."
    predicted = review.get("preparation") in {"sequence_prediction", "fasta_prediction"}
    if predicted:
        for key in ("reference", "reference_structure"):
            if key == "reference_structure" and review.get("preparation") == "fasta_prediction":
                continue
            path = resolve_path(cfg, review.get(key + "_path"))
            if not path or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != review.get(key + "_sha256"):
                raise ValueError(error + " (원본 참조 파일/해시 불일치)")
        if cfg_get(cfg, "target.pdb") or cfg_get(cfg, "structure.provider") != review.get("provider"):
            raise ValueError(error + " (예측 구조 경로 불일치)")
    else:
        path = resolve_path(cfg, cfg_get(cfg, "target.pdb"))
        if not path or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != review.get("target_sha256"):
            raise ValueError(error + " (구조 파일/해시 불일치)")
    if (cfg_get(cfg, "target.chain") != "A"
            or cfg_get(cfg, "target.name") != review.get("target_name")
            or cfg_get(cfg, "target.sequence") != review.get("target_sequence")
            or cfg_get(cfg, "target.fasta")
            or cfg_get(cfg, "truncate.enabled", False)):
        raise ValueError(error + " (표적·서열·전처리 조건 불일치)")
    context = cfg_get(cfg, "target.aux_chains", [])
    if not isinstance(context, list) or any(not isinstance(c, dict) for c in context):
        raise ValueError(error + " (파트너 형식 오류)")
    if [c.get("sequence") for c in context] != review.get("context_sequences"):
        raise ValueError(error + " (파트너 서열/순서 불일치)")
    if [c.get("name") for c in context] != review.get("context_names"):
        raise ValueError(error + " (파트너 이름 불일치)")
    if predicted:
        if cfg_get(cfg, "target.hotspots") != review.get("prediction_hotspots"):
            raise ValueError(error + " (예측 hotspot 번호 불일치)")
    else:
        labels = parse_hotspot_spec(cfg_get(cfg, "target.hotspots"), "A")
        if (any(e["chain"] != "A" or e["kind"] not in {"pdb", None} for e in labels)
                or sorted(e["number"] for e in labels) != review.get("hotspot_positions")):
            raise ValueError(error + " (hotspot 번호 불일치)")
    if design and review.get("design_blockers"):
        raise ValueError("이 입력의 자동 설계는 지원하지 않습니다: " + "; ".join(review["design_blockers"]))
    if "hotspot_excluded_positions" in review and cfg_get(cfg, "hotspots.auto.exclude_positions", []) != review["hotspot_excluded_positions"]:
        raise ValueError(error + " (결합 부위 제외 구간 불일치)")
    if not predicted and review.get("reference_structure_path"):
        path = resolve_path(cfg, review["reference_structure_path"])
        if not path or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != review.get("reference_structure_sha256"):
            raise ValueError(error + " (원본 전체 좌표/해시 불일치)")
