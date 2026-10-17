from __future__ import annotations

import datetime as _dt
import hashlib
import html
import csv
import json
import math
import numbers
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import pandas as pd
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ASSET_ROOT, _external_viewer_url, cfg_get, resolve_run_artifact, read_config_yaml
from .binding_site_assessment import hotspot_assessment
from .hotspots import requests_automatic_hotspots
from .utils.misc import to_native
from .utils.atomic import check_output, write_bytes
from .utils.presentation import portable_metadata
from .vendor_assets import VENDOR_SHA256, VIEWER_ASSETS, LICENSE_ASSETS
from .utils.metric_provenance import metric_provenance
from . import __version__, rank_select, score_complex

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
PACKAGED_ASSET_ROOT = Path(__file__).resolve().parent / "_data"

CANDIDATE_COLUMNS = [
    ("selection_rank", "기록 선별 순위 (엔진 내)"),
    ("rank", "기록 순위 (엔진 내)"),
    ("design_id", "원본 ID"),
    ("variant_id", "원본 변이 ID"),
    ("candidate_id", "원본 모델 ID"),
    ("source_sequence_id", "원본 후보 ID"),
    ("predictor_engine", "예측 엔진"),
    ("design_origin", "생성 엔진"),
    ("ranking_scope", "순위 범위"),
    ("final_evaluated", "최종 평가 기록"),
    ("final_eligible", "기록 적격"),
    ("site_satisfied", "지정 결합 부위 판정 (기록)"),
    ("site_selection_eligible", "지정 결합 부위 포함 적격"),
    ("pose_consensus_status", "동일 후보 자세 일치"),
    ("pose_supporting_engines", "자세 일치 엔진 수"),
    ("pose_quality_supporting_engines", "기록 적격·자세 일치 엔진 수"),
    ("design_consistency_status", "설계·재예측 일치"),
    ("design_binder_rmsd_A", "설계·재예측 VHH RMSD (Å)"),
    ("review_rank", "검토 순위 (동률 허용)"),
    ("review_rank_scope", "검토 순위 범위"),
    ("candidate_review_priority", "후보 검토 판정"),
    ("review_priority", "모델 검토 판정"),
    ("review_priority_reason", "검토 판정 이유"),
    ("cluster_id", "클러스터"),
    ("region", "영역"),
    ("mutation", "돌연변이"),
    ("composite_score", "배치 상대 종합점수"),
    ("ranking_status", "기록 순위 상태"),
    ("ranking_unavailable_reason", "순위 보류 사유"),
    ("ipsae", "ipSAE"),
    ("ipsae_scope", "ipSAE 범위"),
    ("ipsae_chain_a", "ipSAE 체인 1"),
    ("ipsae_chain_b", "ipSAE 체인 2"),
    ("ipsae_scope_source", "ipSAE 범위 출처"),
    ("iptm", "ipTM"),
    ("iptm_scope", "ipTM 범위"),
    ("iptm_source", "ipTM 출처"),
    ("iptm_unavailable_reason", "쌍 ipTM 미평가 사유"),
    ("complex_iptm", "전체 복합체 ipTM"),
    ("ptm", "pTM"),
    ("pdockq", "pDockQ"),
    ("pdockq2", "pDockQ2 (최소)"),
    ("interface_plddt", "계면 pLDDT"),
    ("interface_pae", "계면 PAE (Å)"),
    ("interface_pae_ab", "PAE 행 표적·열 VHH (Å)"),
    ("interface_pae_ba", "PAE 행 VHH·열 표적 (Å)"),
    ("interface_pae_median", "PAE 중앙값 (Å)"),
    ("interface_pae_p90", "PAE 90백분위 (Å)"),
    ("n_hbonds", "N/O 근접 원자쌍"),
    ("n_salt_bridges", "반대 전하 원자쌍"),
    ("n_clashes", "거리 기준 충돌 원자쌍"),
    ("hotspot_contact_frac", "결합 부위 접촉률"),
    ("esm_score", "ESM 서열 점수"),
    ("esm_llr", "ESM ΔLLR"),
    ("esm_mean_logprob", "ESM mean log-prob"),
    ("dev_severe", "심한 위험 수 (5종)"),
    ("dev_flags", "전체 평가 위험 수"),
    ("plate_id", "플레이트 ID"),
    ("plate_position", "웰"),
    ("comparison_status", "절단형·전장형 비교"),
    ("specificity_complete", "반복 비교 완료"),
    ("cleaved_ipsae_min", "절단형 ipSAE 최소"),
    ("full_ipsae_max", "전장형 ipSAE 최대"),
    ("delta_ipsae", "ipSAE 차이"),
    ("terminal_modeling_status", "말단 화학 평가"),
    ("exclusion_reason", "제외 이유"),
]

REASON_LABELS = {
    "clash": "거리 기준 충돌",
    "ipsae": "ipSAE 기준 미통과",
    "iptm": "ipTM 기준 미통과",
    "iptm_unassessed": "쌍 ipTM 미평가",
    "interface_plddt": "계면 pLDDT 기준 미통과",
    "hotspot": "결합 부위 접촉 기준 미통과",
    "developability": "개발가능성 경고 기준 미통과",
    "developability_unassessed": "CDR 개발가능성 미평가",
    "scoring_error": "구조 점수 계산 오류",
    "not_scored": "구조 점수 미평가",
    "prefilter_not_selected": "예측 전 준비·미선정 (모델 미평가 수에 합산하지 않음)",
    "eligibility_unassessed": "적격 판정 미기록·미평가",
    "parent_control": "원본 후보 대조군",
    "wt_control": "야생형 대조군",
    "sequence_prior_prefilter_unmet": "서열 prior 사전필터 기준 미통과 (구조 미평가)",
    "sequence_prior_top_n_not_selected": "서열 prior 평가 한도 밖 (구조 미평가)",
    "sequence_prior_prefilter_unassessed": "서열 prior 사전필터 미평가 (구조 미평가)",
    "wt_control_not_in_model_pool": "WT 대조군 예측 풀에서 제외",
    "first_stage_only": "1단계 구조만 평가·최종 구조 미평가",
    "boltz_failed_or_incomplete": "Boltz-2 최종 평가 실패·미완료",
    "incomplete_comparison": "짝지은 특이성 비교 미완료",
    "cross_binding_risk": "전장형 교차 결합 위험 진단",
}

POSE_STATUS_LABELS = {
    "pose_consensus_status": {
        "multi_engine_supported": "다중 엔진 지지·탐색용", "single_engine_promising": "엔진별 단독 유망·탐색용",
        "supported": "여러 엔진 자세 일치", "quality_support_insufficient": "자세 일치·기록 적격 지지 부족",
        "single_engine": "단일 엔진·비교 부족", "discordant": "엔진 간 자세 불일치",
        "not_assessed": "미평가", "disabled": "평가 비활성",
    },
    "design_consistency_status": {
        "consistent": "설계·재예측 일치", "inconsistent": "설계·재예측 불일치",
        "not_assessed": "미평가", "not_applicable": "적용 안 함", "disabled": "평가 비활성",
    },
    "review_priority": {
        "multi_engine_supported": "다중 엔진 지지·탐색용", "single_engine_promising": "엔진별 단독 유망·탐색용",
        "prioritized": "우선 검토·탐색용", "site_unmet": "지정 결합 부위 미충족·보류",
        "site_not_assessed": "지정 결합 부위 미평가·보류", "confidence_gate_unmet": "기록 적격 미통과·보류",
        "confidence_not_assessed": "기록 적격 미평가·보류", "geometry_not_assessed": "자세 미평가·보류",
        "design_inconsistent": "설계 자세 미재현·보류", "design_not_assessed": "설계 일치 미평가·보류",
        "consensus_insufficient": "자세 일치 지지 부족·보류", "identity_not_assessed": "후보 대응 미평가·보류", "disabled": "평가 비활성",
    },
}


def pose_status_label(row: dict, key: str) -> str:
    """Translate recorded assessments; missing or unknown is never a pass."""
    value = to_native(row.get(key))
    if key in ("site_satisfied", "site_selection_eligible"):
        return "충족" if value is True else "미충족" if value is False else "미평가"
    labels = POSE_STATUS_LABELS.get("review_priority" if key in ("candidate_review_priority", "review_priority_reason") else key, {})
    return labels.get(value, "미평가") if isinstance(value, str) else "미평가"


def metric_record_label(value) -> str:
    labels = {
        "selected_chain_pair": "선택한 체인쌍",
        "selected_target_binder_pair": "선택 표적–VHH 체인쌍",
        "whole_complex": "전체 복합체",
        "legacy_first_recorded_pair": "이전 기록의 첫 체인쌍",
        "native_global_iptm_two_chain_complex": "두 체인 복합체의 원본 전체 ipTM",
        "pair_chains_iptm_chain_labels_directional_minimum": "체인 이름에 대응한 방향별 원본 ipTM 최솟값",
        "boltz2_zero_based_asym_pair_directional_minimum": "Boltz-2 체인 인덱스에 대응한 방향별 ipTM 최솟값",
        "chai1_native_axis_pair_directional_minimum": "Chai-1 원본 체인 축에 대응한 방향별 ipTM 최솟값",
        "opendde_verified_axis_pair_directional_minimum": "OpenDDE 검증된 체인 축에 대응한 방향별 ipTM 최솟값",
        "unavailable": "미평가",
        "not_assessed": "미평가",
        "not_recorded": "미기록",
        "unrecorded": "미기록",
    }
    return labels.get(value, "기록된 범위·출처 (원문 확인)") if isinstance(value, str) and value else "미기록"


def _recorded_bool(value) -> bool | None:
    value = to_native(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    if isinstance(value, (int, float)) and value in (0, 1):
        return value == 1
    return None


def _display_identity(row: dict) -> str | None:
    for key in ("candidate_id", "design_id", "variant_id", "label"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return None


def _source_identity(row: dict) -> str | None:
    recorded = row.get("source_sequence_id")
    if isinstance(recorded, str) and recorded.strip():
        return recorded
    identifier = _display_identity(row)
    if identifier is None:
        return None
    for engine in (row.get("predictor_engine"), row.get("engine"), "colabfold", "boltz2", "chai1", "opendde"):
        if engine and identifier.startswith(str(engine) + "__"):
            return identifier[len(str(engine)) + 2:]
    return identifier


def _report_presentation(scored: pd.DataFrame | None, selected: pd.DataFrame | None, cfg: dict) -> dict:
    """Derive display facts without changing scores, gates, ranks or input frames."""
    rows = _frame_records(scored, limit=len(scored)) if scored is not None else []
    mode = str(cfg_get(cfg, "run.mode", "") or "")
    aliases, sequence_aliases, counters = {}, {}, Counter()
    all_rows = rows + (_frame_records(selected, limit=len(selected)) if selected is not None else [])
    for index, row in enumerate(all_rows):
        identifier = _display_identity(row) or f"row-{index + 1}"
        origin = str(row.get("design_origin") or row.get("parent_engine") or row.get("source") or row.get("source_sequence_id") or identifier).lower()
        prefix = "RF" if "rfantibody" in origin or origin.startswith("rf_") else "BG" if "boltzgen" in origin or origin.startswith("bg_") else ""
        key = (prefix, _source_identity(row) or identifier)
        if key not in sequence_aliases:
            if prefix:
                counters[prefix] += 1
                sequence_aliases[key] = f"{prefix}-{counters[prefix]:02d}"
            else:
                sequence_aliases[key] = _source_identity(row) or identifier
        aliases[identifier] = sequence_aliases[key]
    model_rows = [row for row in rows if "model_record" not in row or _recorded_bool(row.get("model_record")) is True]
    evaluated, eligible, reasons = [], [], Counter()
    for row in rows:
        explicit = "final_evaluated" if "final_evaluated" in row else "evaluated" if "evaluated" in row else None
        assessed = _recorded_bool(row.get(explicit)) if explicit else not bool(row.get("error") or row.get("prediction_error"))
        if any(bool(error.strip()) if isinstance(error, str) else bool(error)
               for error in (row.get("error"), row.get("prediction_error"))):
            assessed = False  # Presentation only: a recorded failure cannot be a completed evaluation.
        is_model = "model_record" not in row or _recorded_bool(row.get("model_record")) is True
        if is_model:
            evaluated.append(assessed)
        decision = _recorded_bool(row.get("final_eligible")) if assessed is True and mode != "complex" else None
        if is_model:
            eligible.append(decision)
        codes = {code.strip() for code in str(row.get("exclusion_reason") or "").split(";") if code.strip()}
        if isinstance(row.get("exclusion_reasons"), list):
            codes.update(str(code) for code in row["exclusion_reasons"] if code)
        if not is_model:
            codes.difference_update({"not_scored", "eligibility_unassessed"})
            codes.add("prefilter_not_selected")
        elif assessed is not True:
            codes.add("not_scored")
        elif mode != "complex" and decision is None:
            codes.add("eligibility_unassessed")
        reasons.update(codes)
    sequences = [row.get("sequence") for row in rows]
    complete_sequences = all(isinstance(value, str) and value.strip() for value in sequences)
    source_ids = [_source_identity(row) for row in rows]
    summary = {
        "unique_sequences": len(set(sequences)) if scored is not None and complete_sequences and mode != "complex" else None,
        "source_candidates": (len(set(source_ids)) if all(source_ids) else len(set(sequences)) if complete_sequences else None) if scored is not None and mode != "complex" else None,
        "candidate_record_total": len(rows) if scored is not None else None,
        "preprediction_record_total": len(rows) - len(model_rows) if scored is not None else None,
        "model_total": len(model_rows) if scored is not None else None,
        "evaluated_total": sum(value is True for value in evaluated) if scored is not None else None,
        "eligible_total": sum(value is True for value in eligible) if scored is not None and mode != "complex" and (not model_rows or any(value is not None for value in eligible)) else None,
        "eligibility_missing": sum(value is None for value in eligible) if scored is not None and mode != "complex" else None,
        "evaluation_missing": sum(value is not True for value in evaluated) if scored is not None else None,
    }
    metric_methods = []
    for row in rows:
        identifier = _display_identity(row) or "미기록"
        for metric, receipt in metric_provenance(row).items():
            if receipt["status"] != "not_assessed":
                metric_methods.append({"candidate_id": identifier, "metric": metric, **receipt})
    return {"summary": summary, "aliases": aliases, "metric_methods": metric_methods,
            "reasons": [{"code": code, "label": REASON_LABELS.get(code, "기록된 제외·미평가 사유"), "count": count} for code, count in reasons.most_common()]}


def _display_groups(rows: list[dict], cfg: dict) -> list[dict]:
    groups = {}
    for row in rows:
        engine = str(row.get("predictor_engine") or row.get("engine") or row.get("ranking_scope") or cfg_get(cfg, "screen.engine", "미기록") or "미기록")
        groups.setdefault(engine, []).append(row)
    return [{"engine": engine, "rows": grouped} for engine, grouped in groups.items()]


HOTSPOT_COLUMNS = [
    ("label", "잔기"),
    ("aa", "AA"),
    ("relative_sasa", "rel-SASA"),
    ("kyte_doolittle", "소수성(KD)"),
    ("conservation", "보존도"),
    ("plddt", "pLDDT"),
    ("dist_to_glycan", "글리칸 거리 (Å)"),
]


def display_number(value):
    """Readable display only; exact source/JSON/CSV values remain untouched."""
    if isinstance(value, numbers.Real) and not isinstance(value, (bool, int)):
        numeric = float(value)
        if not math.isfinite(numeric):
            return "—"
        if numeric == 0:
            return "0"
        # Retain very small nonzero values rather than rounding them to zero.
        return format(numeric, ".8g")
    return value


def metric_scope_records(rows: list[dict], mode: str) -> list[dict]:
    """Label legacy complex summaries without changing their recorded first-pair value."""
    result = []
    for saved in rows:
        row = dict(saved)
        if mode == "complex":
            if not row.get("ipsae_scope"):
                pairs = row.get("pair_metrics")
                first = pairs[0] if isinstance(pairs, list) and pairs and isinstance(pairs[0], dict) else None
                if first and first.get("chain_a") and first.get("chain_b") and row.get("ipsae") == first.get("ipsae"):
                    row.update(ipsae_scope="selected_chain_pair", ipsae_chain_a=first["chain_a"],
                               ipsae_chain_b=first["chain_b"], ipsae_scope_source="legacy_first_recorded_pair")
                else:
                    row["ipsae_scope"] = "not_recorded"
        result.append(row)
    return result


def _selection_display_records(rows: list[dict], selected_rows: list[dict] | None) -> list[dict]:
    """Join only unambiguous stored selection identities; never manufacture a selection rank."""
    def identity(row):
        return _display_identity(row), str(row.get("predictor_engine") or row.get("engine") or "")
    all_counts = Counter(identity(row) for row in rows)
    selected = {}
    for row in selected_rows or []:
        selected.setdefault(identity(row), []).append(row)
    result = []
    for saved in rows:
        row = dict(saved)
        key = identity(row)
        matches = selected.get(key, [])
        by_identifier = [entry for selected_key, entries in selected.items() if selected_key[0] == key[0] for entry in entries]
        ambiguous = False
        if not matches and by_identifier:
            identifier_count = sum(k[0] == key[0] for k in all_counts.elements())
            if (identifier_count == 1 and len(by_identifier) == 1
                    and (not key[1] or not identity(by_identifier[0])[1])):
                matches = by_identifier
            else:
                ambiguous = any(not identity(entry)[1] or not key[1] for entry in by_identifier)
        rank = matches[0].get("selection_rank") if len(matches) == 1 and all_counts[key] == 1 else None
        try:
            number = float(rank) if not isinstance(rank, bool) else float("nan")
            valid = number > 0 and number < float("inf") and number.is_integer()
        except (TypeError, ValueError):
            valid = False
        row["selection_rank"] = (int(number) if valid else "선별 순위 미기록·대응 모호" if matches or ambiguous
                                 else "미선별" if selected_rows is not None else "선별 미기록")
        result.append(row)
    return result


def _truncation_summary(info) -> str:
    """Display recorded truncation facts without paths or internal dictionaries."""
    if not isinstance(info, dict) or type(info.get("enabled")) is not bool:
        return "미기록"
    if not info["enabled"]:
        return "사용 안 함"
    parts = ["사용"]
    for key, label in (("original_residues", "원본 잔기"), ("kept_residues", "유지 잔기"),
                       ("kept_other_chain_residues", "다른 체인 유지 잔기")):
        value = info.get(key)
        if type(value) is int and value >= 0:
            parts.append(f"{label} {value}개")
    for key, label in (("kept_ranges", "유지 구간"), ("missing_hotspots", "누락 결합 부위")):
        values = info.get(key)
        if isinstance(values, list) and values:
            # Labels/ranges are recorded residue identifiers, never file names.
            safe = [str(value) for value in values if re.fullmatch(r"[A-Za-z0-9:,+.\- ]+", str(value))]
            if safe:
                parts.append(f"{label}: {', '.join(safe)}")
    return " · ".join(parts)


def _report_i18n_runtime(document: str) -> str:
    """Inline the shared translator with only this report's owned captions.

    Saved IDs/notes are not localization inputs. A small catalog also keeps the
    static entry report below the existing delivery size budget.
    """
    from html.parser import HTMLParser

    class Captions(HTMLParser):
        def __init__(self):
            super().__init__()
            self.values = set()
            self.active = []
            self.depth = 0

        def handle_starttag(self, tag, attrs):
            self.depth += 1
            attrs = dict(attrs)
            if "data-report-i18n" in attrs:
                self.active.append((self.depth, []))
            if "data-report-i18n-attributes" in attrs:
                for field in ("placeholder", "aria-label", "title"):
                    if attrs.get(field):
                        self.values.add(attrs[field])
            if tag in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
                self.depth -= 1

        def handle_endtag(self, tag):
            for depth, parts in list(self.active):
                if depth == self.depth:
                    self.values.add("".join(parts))
                    self.active.remove((depth, parts))
            self.depth -= 1

        def handle_data(self, text):
            for _, parts in self.active:
                parts.append(text)

    captions = Captions()
    captions.feed(document)
    captions.values.update(match[1] for match in re.findall(r"I18N\.term\(([\"'])(.*?)\1\)", document))
    # A scoped ranking warning keeps its recorded engine prefix as data. Only
    # these authored notice bodies participate in catalog/clause selection.
    for value in tuple(captions.values):
        notice = re.match(r"^(?:colabfold|boltz2|chai1|opendde|mock): ((?:종합점수·기록 순위 보류 |전체 성공 후보에서 미평가인 순위 지표: )[\s\S]+)$", value)
        if notice:
            captions.values.add(notice[1])
    # The translator composes this one authored warning from diagnostic
    # clauses. Select its clause keys as well as the whole message, matching
    # the runtime's sentence boundary exactly. Never split arbitrary captions,
    # original IDs, settings JSON, notes or unmarked preformatted records.
    ranking_clauses = set()
    for value in captions.values:
        if re.match(r"^종합점수·기록 순위 보류 \d+/\d+개 모델\.(?:\s|$)", value):
            ranking_clauses.update(re.split(r"(?<=\.)\s+", value))
    captions.values.update(ranking_clauses)
    source = (TEMPLATE_DIR / "platform-i18n.js").read_text(encoding="utf-8")
    pattern = r"const NCF_TRANSLATIONS = Object.freeze\((\{[\s\S]*?\})\);"
    catalog_match = re.search(pattern, source)
    if catalog_match is None:
        raise ValueError("Packaged report translation catalog is missing")
    catalog = json.loads(catalog_match[1])
    normalized = {" ".join(value.split()) for value in captions.values}
    selected = {}
    for key, value in catalog.items():
        parts = re.split(r"(\{\d+\})", key)
        dynamic = "".join(r"[\s\S]*?" if re.fullmatch(r"\{\d+\}", part) else re.escape(part) for part in parts)
        if " ".join(key.split()) in normalized or (len(parts) > 1 and any(re.fullmatch(dynamic, text) for text in captions.values)):
            selected[key] = value
    source = source[:catalog_match.start(1)] + json.dumps(selected, ensure_ascii=False) + source[catalog_match.end(1):]
    return source.replace("</", "<\\/")


def generate_report(
    run_dir: Path,
    cfg: dict,
    context: dict,
    scored: pd.DataFrame | None = None,
    selected: pd.DataFrame | None = None,
    out_path: Path | None = None,
    viewer_max: int = 5,
) -> Path:
    from .publication import gate_summary
    context = {**context, "handoff_gate": gate_summary(context.get("handoff_gate"))}
    out_path = out_path or (run_dir / "report.html")
    _check_output_path(out_path, out_path.parent)
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    from markupsafe import Markup, escape
    # Only template-owned derived captions use this marker. Raw saved values
    # remain unmarked even when they equal a translation key such as 완료.
    env.globals["report_term"] = lambda value: Markup('<span data-report-i18n>') + escape(str(value)) + Markup('</span>')
    env.filters["display_number"] = display_number
    template = env.get_template("report.html.j2")

    from .platform.results import ResultReader
    saved_reader = ResultReader(run_dir) if run_dir.is_dir() else None
    population = scored
    population_scope = "provided_scored_records"
    if saved_reader and (run_dir / "03_score/scored_candidates.csv").is_file():
        saved_rows = saved_reader.all_rows()
        if saved_rows:
            scored_identities = {_display_identity(row) for row in _frame_records(scored, limit=len(scored))} if scored is not None else set()
            positions = {_display_identity(row): index for index, row in enumerate(_frame_records(scored, limit=len(scored)))} if scored is not None else {}
            for row in saved_rows:
                row.setdefault("model_record", _display_identity(row) in scored_identities)
            saved_rows.sort(key=lambda row: (row.get("model_record") is not True,
                                            positions.get(_display_identity(row), len(positions))))
            population = pd.DataFrame(saved_rows)
            population_scope = "saved_all_candidate_records"
    presentation = _report_presentation(population, selected, cfg)
    all_scored_rows = metric_scope_records(_frame_records(population, limit=len(population)) if population is not None else [],
                                         str(cfg_get(cfg, "run.mode", "")))
    all_selected_rows = _frame_records(selected, limit=len(selected)) if selected is not None else None
    display_rows = _selection_display_records(all_scored_rows, all_selected_rows)
    scored_rows = display_rows[:int(cfg_get(cfg, "report.top_n_table", 50))]
    selected_rows = (all_selected_rows or [])[:50]

    selected_total = int(len(selected)) if selected is not None else 0
    scored_total = int(len(population)) if population is not None else 0
    viewer_mode = str(cfg_get(cfg, "report.viewer", "3dmol")).lower()
    viewer_structures, viewer_script = [], None
    interactive_report_href = None
    explorer_path, explorer = None, None
    if viewer_mode != "none" and saved_reader and saved_reader.all_rows():
        # Keep the entry report small and avoid loading every model/PAE until
        # requested. The sibling explorer remains a complete offline document;
        # moving the two HTML files together preserves the relative link.
        explorer = saved_reader.export("html", report_artifact=True)[0]
        # Report-only commands change execution timestamps and the state-file
        # digest. Reuse the prior immutable bytes when the actual result,
        # publication outcome and renderer have not changed.
        explorer = _reuse_report_explorer(out_path, explorer)
        # A prior entry report must keep its exact linked snapshot even when
        # this refresh fails after publishing the explorer but before the entry.
        digest = hashlib.sha256(explorer).hexdigest()
        explorer_name = out_path.stem + "_explorer-" + digest + ".html"
        if len(explorer_name.encode("utf-8")) > 255:
            explorer_name = "report_explorer-" + digest + ".html"
        explorer_path = out_path.with_name(explorer_name)
        _check_output_path(explorer_path, out_path.parent)
        interactive_report_href = quote(explorer_path.name)
    elif viewer_mode != "none":
        viewer_structures = _viewer_structures(selected, scored, viewer_max, run_dir=run_dir)
        if viewer_structures:
            viewer_script = _viewer_script(out_path.parent, cfg)
    warnings = list(context.get("warnings") or [])
    warnings.extend(score_complex.iptm_missing_warnings(all_scored_rows))
    hotspots_df = _safe_read_csv(run_dir / "01_target" / "hotspot_table.csv", warnings)
    hotspot_sets_df = _safe_read_csv(run_dir / "01_target" / "hotspot_sets.csv", warnings)
    recorded_cfg = {}
    recorded_config_path = run_dir / "run_config.yaml"
    if recorded_config_path.is_file():
        try:
            recorded_cfg = read_config_yaml(recorded_config_path.read_text(encoding="utf-8"))
            if not isinstance(recorded_cfg, dict):
                raise ValueError("설정 객체 필요")
        except (ValueError, OSError, UnicodeError, yaml.YAMLError) as exc:
            warnings.append(f"run_config.yaml을 읽지 못했습니다 ({type(exc).__name__}). 적용 임계값을 미기록으로 표시합니다.")
            recorded_cfg = {}
    assessment = saved_reader.assessment if saved_reader else None
    applied_cfg = (assessment.get("assessment_config") or {}) if assessment else recorded_cfg
    threshold_rows = [row for row in scored_rows if row.get("model_record", True) is True]
    if assessment and saved_reader:
        # Rebuild presentation checks from the verified current assessment only;
        # original prediction settings remain a separate immutable declaration.
        threshold_rows = [{**row, **saved_reader._assessment_details(row)} for row in threshold_rows]
    if population is not None:
        ranking_population = (population.loc[population["model_record"].eq(True)]
                              if "model_record" in population else population)
        warnings.extend(rank_select.ranking_warnings(ranking_population, recorded_cfg or cfg))
    scientific_cfg = recorded_cfg or cfg
    automatic_hotspots = (cfg_get(scientific_cfg, "run.mode") != "complex" and requests_automatic_hotspots(
        cfg_get(scientific_cfg, "target.hotspots", "auto"), cfg_get(scientific_cfg, "target.chain", "A")))
    if automatic_hotspots and hotspots_df.empty:
        table_path = run_dir / "01_target/hotspot_table.csv"
        if not table_path.is_file():
            warnings.append("자동 결합 부위를 요청했지만 hotspot_table.csv 기록 파일이 없습니다. "
                            "평가 결과가 0개라는 뜻으로 해석하지 말고 표적 단계의 원본 산출물을 확인하세요.")
        elif not any("hotspot_table.csv을 읽지 못했습니다" in issue for issue in warnings):
            warnings.append("hotspot_table.csv 파일은 있으나 평가 표가 0행입니다(헤더만 보존). "
                            "자동 결합 부위의 평가 완료나 결합 부위 없음으로 해석하지 않습니다.")
    engine_provenance = saved_reader.engine_provenance() if saved_reader else []
    comparison = saved_reader._json("02_comparison/comparison_status.json") if saved_reader else {}
    expected_models = comparison.get("expected_rows") if isinstance(comparison, dict) else None
    expected_source = "02_comparison/comparison_status.json"
    if expected_models is None and saved_reader and cfg_get(cfg, "run.mode") == "complex":
        phases = saved_reader.state.get("phases", {})
        expected_models = phases.get("prediction", {}).get("total_models")
        if expected_models is None:
            expected_models = phases.get("final", {}).get("total_models")
        expected_source = "run_state.json"
    if type(expected_models) is not int or expected_models < 0:
        expected_models = None
    if saved_reader:
        warnings.extend(saved_reader.issues)
    if viewer_script and urlsplit(viewer_script).scheme:
        warnings.append("명시한 외부 3D 뷰어를 사용합니다. 뷰어 로딩에 인터넷이 필요하며 오프라인 보고서가 아닙니다.")
    render_context = {
        "title": str(cfg_get(cfg, "report.title", "NanoCoFold")),
        "run_dir": run_dir.name,
        "run_id": run_dir.name,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "reporting_version": __version__,
        "computation_version": saved_reader.state.get("application_version") if saved_reader else None,
        "population_scope": population_scope,
        "mode": cfg_get(cfg, "run.mode"),
        "target_name": cfg_get(cfg, "target.name"),
        "context": to_native(context),
        "truncation_summary": _truncation_summary((context.get("target_qc") or {}).get("truncation")),
        "report_summary": presentation["summary"],
        "display_aliases": presentation["aliases"],
        "hotspot_assessment": hotspot_assessment,
        "pose_status_label": pose_status_label,
        "metric_record_label": metric_record_label,
        "pose_assessments_available": any(key in row for row in scored_rows for key in POSE_STATUS_LABELS),
        "display_identity": _display_identity,
        "reason_summary": presentation["reasons"],
        "known_reason_codes": list(REASON_LABELS),
        "metric_method_rows": presentation["metric_methods"],
        "threshold_check_rows": _threshold_check_rows(threshold_rows, applied_cfg),
        "thresholds_source": (assessment["source"] if assessment["status"] == "verified" else assessment["reason"])
                             if assessment else "run_config.yaml" if recorded_cfg else "not_recorded",
        "assessment": assessment,
        "engine_provenance": engine_provenance,
        "expected_models": expected_models,
        "expected_models_source": expected_source,
        "scored_groups": _display_groups(scored_rows, cfg),
        "selected_groups": _display_groups(selected_rows, cfg),
        "run_settings": to_native({key: (recorded_cfg or cfg).get(key) for key in ("run", "target", "structure", "design", "screen", "mature", "complex", "score", "rank") if key in (recorded_cfg or cfg)}),
        "run_settings_source": "run_config.yaml" if recorded_cfg else "report_generation_config",
        "scored_columns": [(key, label) for key, label in CANDIDATE_COLUMNS
                           if any(key in row for row in display_rows) or (selected is not None and key in selected.columns)],
        "scored_rows": scored_rows,
        "selected_rows": selected_rows,
        "hotspot_columns": _hotspot_columns(context, hotspots_df),
        "hotspot_rows": _frame_records(hotspots_df, limit=200),
        "target_residue_total": len(hotspots_df) if hotspots_df is not None else 0,
        "selected_site_residue_total": len({str(value) for value in context.get("hotspots", [])})
                                       if isinstance(context.get("hotspots"), (list, tuple)) else None,
        "hotspot_set_rows": _frame_records(hotspot_sets_df, limit=100),
        "mock_zero_selection": cfg_get(recorded_cfg or cfg, "run.mock") is True
                               and selected is not None and selected_total == 0,
        "mock_cdr_unassessed": sum(row.get("model_record", True) is True and
                                   (row.get("dev_cdr_evaluated") is False or
                                    "developability_unassessed" in str(row.get("exclusion_reason", "")).split(";"))
                                   for row in all_scored_rows),
        "automatic_hotspots": automatic_hotspots,
        "viewer_structures": viewer_structures,
        "interactive_report_href": interactive_report_href,
        "viewer_enabled": viewer_mode != "none",
        "viewer_limit": viewer_max,
        "viewer_script": viewer_script,
        "selected_total": selected_total,
        "scored_csv_available": (run_dir / "03_score/scored_candidates.csv").is_file(),
        "scored_total": scored_total,
        "thresholds": cfg_get(applied_cfg, "score.thresholds", {}) or {},
        "files": _file_index(run_dir),
        "warnings": list(dict.fromkeys(warnings)),
    }
    # Sanitize only presentation metadata. Original computation receipts,
    # coordinates and encoded scientific payloads remain byte-exact.
    file_total = len(render_context["files"])
    render_context["files"] = render_context["files"][:200]
    render_context["files_omitted"] = max(0, file_total - 200)
    render_context = portable_metadata(render_context, root=run_dir)
    # Trusted packaged presentation code is self-contained. Add it after
    # metadata masking so catalog literals are not mistaken for saved paths.
    render_context["report_i18n_script"] = ""
    document = template.render(**render_context)
    slot = '<script id="report-i18n-runtime"></script>'
    if document.count(slot) != 1:
        raise ValueError("Report translation runtime slot is invalid")
    document = document.replace(slot, '<script id="report-i18n-runtime">' + _report_i18n_runtime(document) + '</script>', 1)
    html_bytes = document.encode("utf-8")
    if explorer_path is not None:
        _publish_immutable_explorer(explorer_path, explorer, root=out_path.parent)
    _atomic_write(out_path, html_bytes, prefix=".report-")
    return out_path


def _hotspot_columns(context: dict, hotspots_df: pd.DataFrame | None = None) -> list[tuple[str, str]]:
    metric = str((context.get("target_qc") or {}).get("confidence_metric") or "").lower()
    if not metric and hotspots_df is not None and "confidence_kind" in hotspots_df.columns and not hotspots_df.empty:
        recorded = hotspots_df["confidence_kind"].dropna()
        if not recorded.empty:
            metric = str(recorded.iloc[0]).lower()
    confidence_label = "B-factor" if metric == "bfactor" else "pLDDT"
    return [
        (key, confidence_label if key == "plddt" else label)
        for key, label in HOTSPOT_COLUMNS
    ]


def _threshold_check_rows(rows: list[dict], recorded_cfg: dict) -> list[dict]:
    """Keep row receipts authoritative; never substitute report-time overrides."""
    result = []
    for row in rows:
        checks = row.get("threshold_checks")
        if isinstance(checks, str):
            try:
                checks = json.loads(checks)
            except (ValueError, TypeError):
                checks = None
        if not isinstance(checks, list):
            checks = []
            for metric, key, flag in (("ipsae", "ipsae", "pass_ipsae"), ("iptm", "iptm", "pass_iptm"),
                                     ("interface_plddt", "plddt_interface", "pass_plddt"),
                                     ("hotspot_contact_frac", "hotspot_contact_frac", "pass_hotspot"),
                                     ("n_clashes", "max_clashes", "pass_clash")):
                threshold = cfg_get(recorded_cfg, "score.thresholds." + key)
                checks.append({"metric": metric, "value": row.get(metric), "threshold": threshold,
                               "passed": _recorded_bool(row.get(flag)),
                               "operator": "<=" if metric == "n_clashes" else ">=",
                               "threshold_source": "run_config.yaml" if threshold is not None else "not_recorded"})
        for check in checks:
            if isinstance(check, dict):
                result.append({"model": _display_identity(row), **check})
    return to_native(result)


def _safe_read_csv(path: Path, issues: list[str] | None = None) -> pd.DataFrame:
    if path.exists():
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle, strict=True)
                columns = next(reader)
                if not columns or any(not key for key in columns) or len(set(columns)) != len(columns):
                    raise ValueError("CSV 헤더 오류")
                if any(len(row) != len(columns) for row in reader):
                    raise ValueError("CSV 열 개수 불일치")
            return pd.read_csv(path)
        except (OSError, ValueError, UnicodeError, csv.Error, StopIteration) as exc:
            if issues is not None:
                issues.append(f"{path.name}을 읽지 못했습니다 ({type(exc).__name__}). 해당 결합 부위 표는 미평가이며 원본 파일 복구가 필요합니다.")
            return pd.DataFrame()
    return pd.DataFrame()


def _frame_records(frame: pd.DataFrame | None, limit: int = 50) -> list[dict]:
    if frame is None or frame.empty:
        return []
    subset = frame.head(limit).copy()
    records: list[dict] = []
    for _, row in subset.iterrows():
        record: dict = {}
        for key, value in row.items():
            value = to_native(value)
            if value is pd.NA or value is pd.NaT:
                value = None
            record[key] = value if isinstance(value, (int, float, bool, str, list, dict)) or value is None else str(value)
        records.append(record)
    return records


def _viewer_structures(
    selected: pd.DataFrame | None,
    scored: pd.DataFrame | None,
    limit: int,
    run_dir: Path | None = None,
) -> list[dict]:
    frame = selected if selected is not None and not selected.empty else scored
    if frame is None or frame.empty:
        return []
    structures: list[dict] = []
    for row in _frame_records(frame, limit=limit):
        cif_path = row.get("cif_path")
        candidate_id = str(row.get("design_id") or row.get("variant_id") or row.get("label") or "candidate")
        if run_dir is not None:
            path = resolve_run_artifact(run_dir, cif_path)
        elif isinstance(cif_path, (str, Path)) and cif_path:
            path = Path(str(cif_path))
        else:
            path = None
        if path is None or not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        fmt = "pdb" if path.suffix.lower().endswith(".pdb") else "cif"
        structures.append({
            "id": candidate_id,
            "engine": row.get("predictor_engine") or row.get("engine"),
            "predictor_engine": row.get("predictor_engine"),
            "data": text,
            "format": fmt,
            "target_chain": str(row.get("target_chain") or "A"),
            "binder_chain": str(row.get("binder_chain") or "B"),
        })
    return structures


def _resolve_viewer_asset(cfg: dict, filename: str = "3dmol-min.js", *,
                          asset_root: Path | None = None, local_only: bool = False) -> tuple[bytes | None, dict]:
    """Read workspace then package assets without writing or requesting a URL."""
    external = None if local_only else _external_viewer_url(cfg)
    primary = ASSET_ROOT if asset_root is None else asset_root
    attempts = []
    seen = set()
    primary_origin = "package" if primary == PACKAGED_ASSET_ROOT else "workspace"
    for origin, root in ((primary_origin, primary), ("package", PACKAGED_ASSET_ROOT)):
        local = root / "assets" / filename
        if local in seen:
            continue
        seen.add(local)
        attempt = {"origin": origin, "path": str(local)}
        try:
            content = local.read_bytes()
        except OSError as exc:
            attempt.update(status="missing" if isinstance(exc, FileNotFoundError) else "unreadable",
                           error=f"{type(exc).__name__}: {exc}")
            attempts.append(attempt)
            continue
        digest = hashlib.sha256(content).hexdigest()
        expected = VENDOR_SHA256.get(filename)
        valid = bool(content) and expected is not None and digest == expected
        attempt.update(status="ready" if valid else "empty" if not content else "integrity_mismatch",
                       size_bytes=len(content), sha256=digest, expected_sha256=expected)
        attempts.append(attempt)
        if valid:
            return content, {"ok": True, "stage": "local_viewer_asset", "source": str(local),
                             "origin": origin, "size_bytes": len(content),
                             "sha256": hashlib.sha256(content).hexdigest(),
                             "requires_internet": False, "attempts": attempts}
    if external is not None:
        return None, {"ok": True, "stage": "external_viewer_opt_in", "source": external,
                      "requires_internet": True, "attempts": attempts,
                      "note": "명시한 외부 뷰어를 사용합니다. 로딩에는 인터넷이 필요합니다."}
    detail = "; ".join(f"{attempt['path']} ({attempt['status']})" for attempt in attempts)
    return None, {"ok": False, "stage": "missing_viewer_asset", "source": None,
                  "requires_internet": False, "attempts": attempts,
                  "blocker": f"오프라인 뷰어 자산이 없거나 비어 있거나 읽을 수 없습니다: {detail}. "
                             "NanoCoFold 설치를 복구하거나 report.viewer=none으로 뷰어를 끄세요."}


def viewer_asset_readiness(cfg: dict) -> dict:
    """Return the same read-only asset evidence used during report generation."""
    if str(cfg_get(cfg, "report.viewer", "3dmol")).lower() == "none":
        return {"ok": True, "stage": "viewer_disabled", "source": None,
                "requires_internet": False, "attempts": []}
    _, evidence = _resolve_viewer_asset(cfg)
    return evidence


def delivery_asset_readiness(cfg: dict) -> dict:
    """Automatic ZIP delivery needs every local viewer and required notice."""
    evidence = {name: _resolve_viewer_asset(cfg, name, local_only=True)[1]
                for name in VIEWER_ASSETS + LICENSE_ASSETS}
    return {"ok": all(item["ok"] for item in evidence.values()), "assets": evidence,
            "requires_internet": False, "stage": "offline_delivery_assets",
            "blocker": "전달용 보고서 자산·라이선스 무결성 확인 실패: " +
                       ", ".join(name for name, item in evidence.items() if not item["ok"])}


def _check_output_path(destination: Path, root: Path | None = None) -> None:
    check_output(destination, root)


def _atomic_write(destination: Path, content: bytes, *, prefix: str, root: Path | None = None) -> None:
    write_bytes(destination, content, prefix=prefix, root=root)


def _publish_immutable_explorer(destination: Path, content: bytes, *, root: Path) -> None:
    """Callers hold the run lock; never replace an existing linked snapshot."""
    _check_output_path(destination, root)
    try:
        existing = destination.read_bytes()
    except FileNotFoundError:
        _atomic_write(destination, content, prefix=".report-explorer-", root=root)
        return
    if existing != content:
        raise OSError(f"내용 해시 탐색 파일에 다른 내용이 있습니다. 기존 파일을 보존했습니다: {destination}")


def _explorer_result_identity(content: bytes) -> bytes | None:
    """Compare scientific content and exact assets without report-clock churn.

    Original source state/hashes remain in the immutable HTML and on disk.
    Only this comparison projection excludes operational execution history.
    Authoritative summary status/issues and all phase/result data remain.
    """
    match = re.search(rb'(<script id="result-data" type="application/json">)(.*?)(</script>)', content, re.S)
    if not match:
        return None
    try:
        payload = json.loads(match[2])
        for summary in (payload["summary"], payload["provenance"]["summary"]):
            for key in ("report_artifact", "delivery_artifact", "source_report_status", "source_delivery_status"):
                summary.pop(key, None)
            state = summary.get("state", {})
            for key in ("execution", "previous_execution", "report_source_execution", "current_phase"):
                state.pop(key, None)
            final = state.get("phases", {}).get("final", {})
            for key in ("report", "report_status", "report_error", "delivery_status", "delivery_error", "delivery"):
                final.pop(key, None)
        provenance = payload["provenance"]
        provenance["source_artifacts"] = [row for row in provenance.get("source_artifacts", [])
                                          if row.get("path") != "run_state.json"]
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    digest = hashlib.sha256(content[:match.start(2)])
    digest.update(canonical)
    digest.update(content[match.end(2):])
    return digest.digest()


def report_explorer_path(entry: Path, *, entry_content: bytes | None = None) -> Path | None:
    """Resolve only the saved report's sibling explorer; never external URLs."""
    class Link(HTMLParser):
        href = None

        def handle_starttag(self, tag, attributes):
            attributes = dict(attributes)
            if tag == "a" and attributes.get("id") == "complete-result-explorer":
                self.href = attributes.get("href")

    try:
        parser = Link()
        parser.feed(entry_content.decode("utf-8") if entry_content is not None else entry.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not parser.href:
        return None
    url = urlsplit(parser.href)
    name = unquote(url.path)
    if (url.scheme or url.netloc or url.query or url.fragment or
            not re.fullmatch(r"[^/\\]+_explorer-[a-f0-9]{64}\.html|report_explorer\.html", name)):
        return None
    previous = entry.parent / name
    _check_output_path(previous, entry.parent)
    return previous


def report_snapshot_files(entry: Path) -> dict[str, bytes] | None:
    """Bundle the exact saved entry and linked snapshot without re-scoring."""
    entry_content = entry.read_bytes()
    explorer = report_explorer_path(entry, entry_content=entry_content)
    if explorer is None:
        return None
    content = explorer.read_bytes()
    match = re.search(r"_explorer-([a-f0-9]{64})\.html$", explorer.name)
    if match and hashlib.sha256(content).hexdigest() != match[1]:
        raise OSError("내용 해시 탐색 파일에 다른 내용이 있습니다. 기존 파일을 보존했습니다.")
    return {entry.name: entry_content, explorer.name: content}


def _reuse_report_explorer(entry: Path, fresh: bytes) -> bytes:
    previous = report_explorer_path(entry)
    if previous is None:
        return fresh
    match = re.search(r"_explorer-([a-f0-9]{64})\.html$", previous.name)
    if not match:
        return fresh
    try:
        old = previous.read_bytes()
    except FileNotFoundError:
        return fresh
    if hashlib.sha256(old).hexdigest() != match[1]:
        raise OSError("내용 해시 탐색 파일에 다른 내용이 있습니다. 기존 파일을 보존했습니다.")
    identity = _explorer_result_identity(fresh)
    return old if identity is not None and identity == _explorer_result_identity(old) else fresh


def _viewer_script(run_dir: Path, cfg: dict) -> str:
    """Copy the first readable local viewer; external loading requires opt-in."""
    content, evidence = _resolve_viewer_asset(cfg)
    if not evidence["ok"]:
        raise OSError(evidence["blocker"])
    if content is None:
        return evidence["source"]
    destination = run_dir / "assets" / "3dmol-min.js"
    _check_output_path(destination, run_dir)
    try:
        current = destination.read_bytes()
    except OSError:
        current = None
    if current == content:
        return "assets/3dmol-min.js"
    try:
        _atomic_write(destination, content, prefix=".3dmol-", root=run_dir)
    except OSError as exc:
        raise type(exc)(f"오프라인 뷰어 출력 저장 실패: {destination}. 출력 권한·디렉터리를 확인하세요: {exc}") from exc
    return "assets/3dmol-min.js"


def _file_index(run_dir: Path) -> list[dict]:
    entries: list[dict] = []
    if not run_dir.exists():
        return entries
    for path in sorted(run_dir.rglob("*")):
        if (path.is_dir() or path.name.endswith(".lock")
                or path.name.startswith("report") and path.suffix.lower() in {".html", ".zip"}):
            continue
        try:
            size_bytes = path.stat().st_size
            size_kb = round(size_bytes / 1024.0, 1)
        except OSError:
            continue
        entries.append({
            "path": str(path.relative_to(run_dir)),
            "size_kb": size_kb,
            "size_bytes": size_bytes,
            "kind": path.suffix.lower().lstrip("."),
        })
    return entries


def escape(value) -> str:
    return html.escape(str(value))
