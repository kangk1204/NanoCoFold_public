from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import cfg_get
from .handoff_validation import validate_handoff as validate_handoff
from .utils import seq as seq_utils
from .utils.atomic import atomic_writer, write_csv, write_bytes
from .utils.spreadsheet import csv_cell, force_text

__all__ = ["SYNTHESIS_COLUMNS", "validate_handoff", "write_pymol_session_script", "write_synthesis_sheet"]

SYNTHESIS_COLUMNS = [
    "selection_rank", "plate_id", "plate_position", "design_id", "variant_id", "cluster_id", "region",
    "final_eligible", "exclusion_reason",
    "mutation", "composite_score", "ipsae", "iptm", "interface_pae", "n_hbonds",
    "n_clashes", "hotspot_contact_frac", "esm_score", "esm_llr", "esm_mean_logprob", "esm_source", "antifold",
    "pI", "net_charge", "mw_kda", "dev_severe", "dev_warnings", "sequence", "sequence_sha256",
    "dev_cdr_evaluated",
    "comparison_status", "specificity_complete", "evidence_type",
    "terminal_modeling_status", "review_status",
    "construct_aa", "construct_dna", "dna_status", "notes",
]


def write_synthesis_sheet(selected: pd.DataFrame, cfg: dict, out_dir: Path, sequence_column: str = "sequence",
                          metadata_columns: tuple[str, ...] = ()) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    expression = cfg_get(cfg, "expression", {}) or {}
    signal = str(expression.get("signal_peptide", "") or "")
    tag = str(expression.get("tag", "") or "")
    host = str(expression.get("host", "ecoli_periplasmic"))
    optimize = bool(expression.get("codon_optimize", True))
    organism = str(expression.get("organism_codons", "ecoli"))
    dna_status = "preferred_codon_draft" if optimize else "not_requested"

    rows: list[dict] = []
    for _, row in selected.iterrows():
        sequence = str(row.get(sequence_column, ""))
        construct = seq_utils.suggest_construct(sequence, signal, tag, host, optimize, organism)
        notes = []
        if row.get("dev_severe", 0):
            notes.append(f"개발가능성 경고 {row.get('dev_warnings', '')}")
        if pd.notna(row.get("n_clashes", float("nan"))) and float(row.get("n_clashes", 0)) > 0:
            notes.append("계면 clash 존재")
        rows.append({
            "selection_rank": row.get("selection_rank"),
            "plate_id": row.get("plate_id", "plate_01"),
            "plate_position": row.get("plate_position"),
            "design_id": row.get("design_id", row.get("variant_id")),
            "final_eligible": bool(row.get("final_eligible", False)),
            "exclusion_reason": row.get("exclusion_reason", ""),
            "variant_id": row.get("variant_id"),
            "cluster_id": row.get("cluster_id"),
            "region": row.get("region"),
            "mutation": row.get("mutation"),
            "composite_score": row.get("composite_score"),
            "ipsae": row.get("ipsae"),
            "iptm": row.get("iptm"),
            "interface_pae": row.get("interface_pae"),
            "n_hbonds": row.get("n_hbonds"),
            "n_clashes": row.get("n_clashes"),
            "hotspot_contact_frac": row.get("hotspot_contact_frac"),
            "esm_score": row.get("esm_score"),
            "esm_llr": row.get("esm_llr"),
            "esm_mean_logprob": row.get("esm_mean_logprob"),
            "esm_source": row.get("esm_source"),
            "antifold": row.get("antifold"),
            "pI": row.get("pI"),
            "net_charge": row.get("net_charge"),
            "mw_kda": row.get("mw_kda"),
            "dev_severe": row.get("dev_severe"),
            "dev_warnings": row.get("dev_warnings"),
            "sequence": sequence,
            "sequence_sha256": row.get("sequence_sha256"),
            "dev_cdr_evaluated": row.get("dev_cdr_evaluated"),
            "comparison_status": row.get("comparison_status"),
            "specificity_complete": row.get("specificity_complete"),
            "evidence_type": row.get("evidence_type"),
            "terminal_modeling_status": row.get("terminal_modeling_status"),
            "review_status": row.get("review_status"),
            "construct_aa": construct if not optimize else seq_utils.suggest_construct(sequence, signal, tag, host, False),
            "construct_dna": construct if optimize else "",
            "dna_status": row.get("dna_status", dna_status),
            "notes": "; ".join(notes),
            **{column: row.get(column) for column in metadata_columns},
        })

    sheet = pd.DataFrame(rows)
    if sheet.empty:
        sheet = pd.DataFrame(columns=SYNTHESIS_COLUMNS + list(metadata_columns))
    else:
        sheet = sheet[[column for column in SYNTHESIS_COLUMNS + list(metadata_columns) if column in sheet.columns]]
    csv_path = out_dir / "synthesis_plate_list.csv"
    write_csv(csv_path, sheet.map(csv_cell), root=out_dir)

    xlsx_path = out_dir / "synthesis_plate_list.xlsx"
    try:
        with atomic_writer(xlsx_path, root=out_dir) as stream, pd.ExcelWriter(stream, engine="openpyxl") as writer:
            sheet.to_excel(writer, sheet_name="order", index=False)
            pd.DataFrame({
                "항목": ["표적", "발현 방식", "신호펩티드", "태그", "코돈 최적화", "주의"],
                "값": [
                    cfg_get(cfg, "target.name", ""),
                    host,
                    signal or "없음",
                    tag or "없음",
                    "예" if optimize else "아니오",
                    "construct_dna는 preferred-codon draft입니다. 합성 발주 전 벡터 호환 오버행/프라이머와 최종 코돈 최적화 확인 필요",
                ],
            }).to_excel(writer, sheet_name="notes", index=False)
            force_text(writer.book)
    except ImportError:
        xlsx_path = None

    return {"csv": str(csv_path), "xlsx": str(xlsx_path) if xlsx_path else None, "n_candidates": len(sheet)}


def write_pymol_session_script(
    top_structures: list[tuple[str, str, str]],
    out_path: Path,
    target_chain: str = "A",
) -> str:
    """top_structures: [(name, structure_path, binder_chain), ...] — binder 체인은 실제 값 사용."""
    lines = [
        "# NanoCoFold PyMOL 시각화 스크립트 (오픈소스 PyMOL)",
        "bg_color white",
        "hide everything",
    ]
    for name, path, binder_chain in top_structures:
        import re
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", str(name)):
            raise ValueError("PyMOL 객체 이름은 영문·숫자·밑줄만 지원합니다.")
        if any(character in str(path) for character in "\n\r;\x00"):
            raise ValueError("PyMOL 구조 경로에 명령 구분 문자를 사용할 수 없습니다.")
        import json
        lines.extend([
            f"load {json.dumps(str(path))}, {name}",
            f"show cartoon, {name}",
            f"color grey80, {name} and chain {target_chain}",
            f"color skyblue, {name} and chain {binder_chain}",
            f"select interface_{name}, {name} within 5 of chain {target_chain}",
            f"show sticks, interface_{name} and chain {binder_chain}",
        ])
    lines.append("zoom all")
    write_bytes(out_path, ("\n".join(lines) + "\n").encode("utf-8"))
    return str(out_path)
