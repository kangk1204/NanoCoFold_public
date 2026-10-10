from __future__ import annotations

import datetime as _dt
import hashlib
import html
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ASSET_ROOT, _external_viewer_url, cfg_get, resolve_run_artifact
from .binding_site_assessment import hotspot_assessment
from .hotspots import requests_automatic_hotspots
from .utils.misc import to_native
from .utils.atomic import check_output, write_bytes
from .vendor_assets import VENDOR_SHA256, VIEWER_ASSETS, LICENSE_ASSETS
from .utils.metric_provenance import metric_provenance

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
    ("site_satisfied", "지정 부위 충족"),
    ("site_selection_eligible", "지정 부위 포함 적격"),
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
    ("ipsae", "ipSAE"),
    ("iptm", "ipTM"),
    ("ptm", "pTM"),
    ("pdockq", "pDockQ"),
    ("pdockq2", "pDockQ2 (최소)"),
    ("interface_plddt", "계면 pLDDT"),
    ("interface_pae", "계면 PAE"),
    ("interface_pae_ab", "PAE 행 표적·열 VHH (Å)"),
    ("interface_pae_ba", "PAE 행 VHH·열 표적 (Å)"),
    ("interface_pae_median", "PAE 중앙값 (Å)"),
    ("interface_pae_p90", "PAE 90백분위 (Å)"),
    ("n_hbonds", "N/O 근접 원자쌍"),
    ("n_salt_bridges", "반대 전하 원자쌍"),
    ("n_clashes", "거리 기준 충돌 원자쌍"),
    ("hotspot_contact_frac", "핫스팟 접촉률"),
    ("esm_score", "ESM 서열 prior (친화도 아님)"),
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
    ("delta_ipsae", "ipSAE 차이(KD 아님)"),
    ("terminal_modeling_status", "말단 화학 평가"),
    ("exclusion_reason", "제외 이유"),
]

REASON_LABELS = {
    "clash": "거리 기준 충돌",
    "ipsae": "ipSAE 기준 미통과",
    "iptm": "ipTM 기준 미통과",
    "interface_plddt": "계면 pLDDT 기준 미통과",
    "hotspot": "핫스팟 접촉 기준 미통과",
    "developability": "개발가능성 경고 기준 미통과",
    "developability_unassessed": "CDR 개발가능성 미평가",
    "scoring_error": "구조 점수 계산 오류",
    "not_scored": "구조 점수 미평가",
    "eligibility_unassessed": "적격 판정 미기록·미평가",
    "parent_control": "원본 후보 대조군",
    "wt_control": "야생형 대조군",
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
        "prioritized": "우선 검토·탐색용", "site_unmet": "지정 부위 미충족·보류",
        "site_not_assessed": "지정 부위 미평가·보류", "confidence_gate_unmet": "기록 적격 미통과·보류",
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
    for engine in (row.get("predictor_engine"), "colabfold", "boltz2", "chai1"):
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
    evaluated, eligible, reasons = [], [], Counter()
    for row in rows:
        explicit = "final_evaluated" if "final_evaluated" in row else "evaluated" if "evaluated" in row else None
        assessed = _recorded_bool(row.get(explicit)) if explicit else not bool(row.get("error") or row.get("prediction_error"))
        evaluated.append(assessed)
        decision = _recorded_bool(row.get("final_eligible")) if assessed is True and mode != "complex" else None
        eligible.append(decision)
        codes = {code.strip() for code in str(row.get("exclusion_reason") or "").split(";") if code.strip()}
        if assessed is not True:
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
        "model_total": len(rows) if scored is not None else None,
        "evaluated_total": sum(value is True for value in evaluated) if scored is not None else None,
        "eligible_total": sum(value is True for value in eligible) if scored is not None and mode != "complex" and (not rows or any(value is not None for value in eligible)) else None,
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
    ("dist_to_glycan", "글리칸 거리"),
]


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
    template = env.get_template("report.html.j2")

    presentation = _report_presentation(scored, selected, cfg)
    scored_rows = _frame_records(scored, limit=int(cfg_get(cfg, "report.top_n_table", 50)))
    selected_rows = _frame_records(selected, limit=50)

    viewer_structures = _viewer_structures(selected, scored, viewer_max, run_dir=run_dir)
    selected_total = int(len(selected)) if selected is not None else 0
    scored_total = int(len(scored)) if scored is not None else 0
    viewer_mode = str(cfg_get(cfg, "report.viewer", "3dmol")).lower()
    viewer_script = _viewer_script(out_path.parent, cfg) if (viewer_mode != "none" and viewer_structures) else None
    if viewer_mode == "none":
        viewer_structures = []
    hotspots_df = _safe_read_csv(run_dir / "01_target" / "hotspot_table.csv")
    hotspot_sets_df = _safe_read_csv(run_dir / "01_target" / "hotspot_sets.csv")

    warnings = list(context.get("warnings") or [])
    if viewer_script and urlsplit(viewer_script).scheme:
        warnings.append("명시한 외부 3D 뷰어를 사용합니다. 뷰어 로딩에 인터넷이 필요하며 오프라인 보고서가 아닙니다.")
    render_context = {
        "title": str(cfg_get(cfg, "report.title", "NanoCoFold")),
        "run_dir": str(run_dir),
        "run_id": run_dir.name,
        "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": cfg_get(cfg, "run.mode"),
        "target_name": cfg_get(cfg, "target.name"),
        "context": to_native(context),
        "report_summary": presentation["summary"],
        "display_aliases": presentation["aliases"],
        "hotspot_assessment": hotspot_assessment,
        "pose_status_label": pose_status_label,
        "pose_assessments_available": any(key in row for row in scored_rows for key in POSE_STATUS_LABELS),
        "display_identity": _display_identity,
        "reason_summary": presentation["reasons"],
        "metric_method_rows": presentation["metric_methods"],
        "scored_groups": _display_groups(scored_rows, cfg),
        "selected_groups": _display_groups(selected_rows, cfg),
        "run_settings": to_native({key: cfg.get(key) for key in ("run", "target", "structure", "design", "screen", "mature", "complex", "score", "rank") if key in cfg}),
        "scored_columns": [(key, label) for key, label in CANDIDATE_COLUMNS
                           if (scored is not None and key in scored.columns) or (selected is not None and key in selected.columns)],
        "scored_rows": scored_rows,
        "selected_rows": selected_rows,
        "hotspot_columns": _hotspot_columns(context, hotspots_df),
        "hotspot_rows": _frame_records(hotspots_df, limit=200),
        "hotspot_set_rows": _frame_records(hotspot_sets_df, limit=100),
        "automatic_hotspots": requests_automatic_hotspots(
            cfg_get(cfg, "target.hotspots", "auto"), cfg_get(cfg, "target.chain", "A")),
        "viewer_structures": viewer_structures,
        "viewer_script": viewer_script,
        "selected_total": selected_total,
        "scored_total": scored_total,
        "thresholds": cfg_get(cfg, "score.thresholds", {}) or {},
        "files": _file_index(run_dir),
        "warnings": warnings,
    }
    html_text = template.render(**render_context)
    _atomic_write(out_path, html_text.encode("utf-8"), prefix=".report-")
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
        if key != "confidence_kind"
    ]


def _safe_read_csv(path: Path) -> pd.DataFrame:
    if path.exists():
        try:
            return pd.read_csv(path)
        except Exception:  # noqa: BLE001
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
        if path.is_dir():
            continue
        try:
            size_kb = round(path.stat().st_size / 1024.0, 1)
        except OSError:
            continue
        entries.append({
            "path": str(path.relative_to(run_dir)),
            "size_kb": size_kb,
            "kind": path.suffix.lower().lstrip("."),
        })
    return entries[:200]


def escape(value) -> str:
    return html.escape(str(value))
