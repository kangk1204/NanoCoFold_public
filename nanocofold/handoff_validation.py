from __future__ import annotations

from pathlib import Path
from typing import Any

import json
import hashlib
import pandas as pd
import yaml
from Bio.Seq import Seq

from .config import cfg_get
from .utils import seq as seq_utils


_PELB_SIGNAL = "MKYLLPTAAAGLLLLAAQPAMA"  # 대장균 periplasm 신호펩티드(config/default.yaml 기본값)
_INCOMPLETE_STATUS = {"", "na", "n/a", "none", "null", "pending", "unassessed", "incomplete", "todo", "unknown"}
_BAD_SPECIFICITY_STATUS = {
    "cross_binding_risk",
    "fail",
    "failed",
    "insufficient",
    "obstructed",
    "obstructing",
    "blocked",
    "nonspecific",
}
_DNA_BASES = {"A", "C", "G", "T"}
REQUIRED_CONTROLS = {"full_antigen", "cleaved_antigen", "unrelated_vhh", "tag_control", "blank_reference"}


def validate_handoff(run_dir: Path) -> dict:
    """Read-only handoff gate for synthesis/expression-ready runs.

    The validator intentionally does not mutate run artifacts. It checks the
    saved run config plus the generated synthesis plate list and returns a
    structured report that callers can decide to block on.
    """
    run_dir = Path(run_dir)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    details: dict[str, Any] = {
        "run_dir": str(run_dir),
        "config_path": str(run_dir / "run_config.yaml"),
        "synthesis_csv": str(run_dir / "05_final" / "synthesis_plate_list.csv"),
        "n_synthesis_rows": 0,
    }

    cfg = _load_config(run_dir / "run_config.yaml", errors)
    sheet = _load_synthesis(run_dir / "05_final" / "synthesis_plate_list.csv", errors)
    if sheet is not None:
        details["n_synthesis_rows"] = int(len(sheet))

    specificity_enabled = bool(cfg_get(cfg, "specificity.enabled", False)) if cfg is not None else False
    if cfg is not None:
        _validate_required_config(cfg, errors)
        _validate_specificity_config(cfg, run_dir, errors, warnings)
        _warn_draft_dna_label(cfg, warnings)
    if sheet is not None:
        if sheet.empty:
            _add(errors, "empty_synthesis_sheet", "synthesis_plate_list.csv has no order rows.")
        _validate_row_eligibility(sheet, errors)
        _validate_construct_sequences(sheet, cfg or {}, errors, warnings)
        _validate_specificity_status(run_dir, specificity_enabled, errors)
        current_review_identities = _current_review_identities(sheet) if specificity_enabled else set()
        approved_reviews = _load_candidate_reviews(run_dir, specificity_enabled, current_review_identities, errors)
        terminal_review_confirmed = cfg is not None and cfg_get(cfg, "experiment.terminal_review_confirmed") is True
        _validate_specificity_rows(
            sheet, specificity_enabled, approved_reviews, errors, warnings,
            terminal_review_confirmed=terminal_review_confirmed,
        )
        _validate_controls(sheet, run_dir, specificity_enabled, errors)

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        **details,
    }


def _load_config(path: Path, errors: list[dict[str, Any]]) -> dict | None:
    if not path.exists():
        _add(errors, "missing_run_config", f"Missing run_config.yaml: {path}")
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        _add(errors, "invalid_run_config", f"Could not read run_config.yaml: {exc}")
        return None
    if not isinstance(data, dict):
        _add(errors, "invalid_run_config", "run_config.yaml must contain a mapping.")
        return None
    return data


def _load_synthesis(path: Path, errors: list[dict[str, Any]]) -> pd.DataFrame | None:
    if not path.exists():
        _add(errors, "missing_synthesis_csv", f"Missing synthesis plate list: {path}")
        return None
    try:
        return pd.read_csv(path, dtype={"design_id": str, "variant_id": str, "sequence_sha256": str})
    except Exception as exc:  # noqa: BLE001
        _add(errors, "invalid_synthesis_csv", f"Could not read synthesis plate list: {exc}")
        return None


def _validate_required_config(cfg: dict, errors: list[dict[str, Any]]) -> None:
    required_strings = [
        ("expression.host", "missing_expression_host"),
        ("expression.vector", "missing_expression_vector"),
        ("experiment.assay", "missing_experiment_assay"),
    ]
    required_true = [
        ("expression.vendor_constraints_reviewed", "vendor_constraints_not_reviewed"),
        ("expression.construct_reviewed", "construct_not_reviewed"),
        ("experiment.antigen_qc_confirmed", "antigen_qc_not_confirmed"),
        ("experiment.controls_confirmed", "controls_not_confirmed"),
    ]

    for dotted, code in required_strings:
        value = cfg_get(cfg, dotted)
        if not isinstance(value, str) or _is_blank(value):
            _add(errors, code, f"Required handoff config must be an explicit string: {dotted}", path=dotted)
    for dotted, code in required_true:
        if cfg_get(cfg, dotted) is not True:
            _add(errors, code, f"Required handoff confirmation must be true: {dotted}", path=dotted)


def _validate_construct_sequences(
    sheet: pd.DataFrame,
    cfg: dict,
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    order_format = str(cfg_get(cfg, "expression.order_format", "dna") or "dna").strip().lower()
    if order_format not in {"dna", "aa"}:
        _add(errors, "invalid_order_format", "expression.order_format must be dna or aa.")
    if "construct_dna" not in sheet.columns:
        if order_format == "dna":
            _add(errors, "missing_construct_dna_column", "DNA order format requires construct_dna.")
        else:
            _add(warnings, "missing_construct_dna_column", "No construct_dna column was found.")
    if "construct_aa" not in sheet.columns:
        _add(errors, "missing_construct_aa_column", "construct_dna cannot be verified without construct_aa.")
        return
    if "sequence" not in sheet.columns:
        _add(errors, "missing_sequence_column", "construct_aa cannot be verified without sequence.")
        return

    expression = cfg_get(cfg, "expression", {}) or {}
    signal = str(expression.get("signal_peptide", "") or "")
    tag = str(expression.get("tag", "") or "")
    host = str(expression.get("host", "ecoli_periplasmic"))
    if host.strip().lower() == "mammalian" and _clean_aa(signal) == _PELB_SIGNAL:
        # pelB 는 대장균 periplasm 신호펩티드다. 포유류 발현 construct 에 그대로 들어가면 분비되지 않는다.
        _add(errors, "signal_peptide_host_mismatch",
             "expression.host=mammalian 인데 신호펩티드가 대장균 pelB 입니다. 포유류 분비 신호펩티드로 바꾸세요.")

    for index, row in sheet.iterrows():
        design_id = _row_id(row, index)
        sequence = _clean_aa(row.get("sequence"))
        if not sequence or set(sequence) - set(seq_utils.AA1):
            _add(errors, "invalid_vhh_sequence", f"{design_id}: sequence must contain standard amino acids.", row=index)
        # 아미노산 서열은 대소문자를 구분하지 않는다. 설정의 소문자 태그/신호펩티드가
        # 정상 construct 를 불일치로 되돌리지 않도록 양쪽을 같은 방식으로 정규화한다.
        expected_aa = _clean_aa(seq_utils.suggest_construct(sequence, signal, tag, host, False))
        aa = _clean_aa(row.get("construct_aa"))
        if aa != expected_aa:
            _add(
                errors,
                "construct_aa_mismatch",
                f"{design_id}: construct_aa does not equal signal_peptide + sequence + tag from run_config.",
                row=index,
            )

        dna = _clean_dna(row.get("construct_dna"))
        if not dna:
            if order_format == "dna":
                _add(errors, "missing_construct_dna", f"{design_id}: expression.order_format=dna requires construct_dna.", row=index)
            else:
                _add(warnings, "missing_construct_dna", "construct_dna is empty; DNA translation was not checked.", row=index)
            continue

        invalid_bases = sorted(set(dna) - _DNA_BASES)
        if invalid_bases:
            _add(
                errors,
                "invalid_construct_dna",
                f"{design_id}: construct_dna contains non-ACGT bases: {','.join(invalid_bases)}.",
                row=index,
            )
            continue
        if len(dna) % 3:
            _add(errors, "dna_frame_mismatch", f"{design_id}: construct_dna length is not a multiple of 3.", row=index)
            continue

        codons = [dna[pos : pos + 3] for pos in range(0, len(dna), 3)]
        stop_positions = [i for i, codon in enumerate(codons) if str(Seq(codon).translate()) == "*"]
        if not stop_positions:
            _add(errors, "dna_stop_mismatch", f"{design_id}: construct_dna has no terminal stop codon.", row=index)
            continue
        if stop_positions != [len(codons) - 1]:
            _add(errors, "dna_stop_mismatch", f"{design_id}: construct_dna has internal or extra stop codons.", row=index)
            continue

        translated = str(Seq(dna).translate(to_stop=True))
        if translated != aa:
            _add(
                errors,
                "dna_translation_mismatch",
                f"{design_id}: construct_dna translation does not match construct_aa.",
                row=index,
            )


def _validate_specificity_config(
    cfg: dict,
    run_dir: Path,
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    enabled = bool(cfg_get(cfg, "specificity.enabled", False))
    if enabled:
        try:
            from . import specificity

            specificity.resolve_definition(cfg)
        except Exception as exc:  # noqa: BLE001
            _add(errors, "invalid_specificity_definition", f"specificity target definition is invalid: {exc}")

    mode = str(cfg_get(cfg, "specificity.epitope_mode", "") or "").strip().lower()
    retain = str(cfg_get(cfg, "specificity.cleavage.retain", "") or "").strip().upper()
    if not enabled and not mode and not retain:
        return
    # run_config.yaml 은 default.yaml 이 합쳐진 전체 설정이다. specificity 를 켜지 않았고 기본값
    # (epitope_mode=both, retain 없음, 항원 태그 전부 미확정)을 건드리지 않았다면 일반 design/screen/mature
    # run 이므로 specificity 전용 항목(말단 검토·항원 태그)을 요구하지 않는다.
    if not enabled and not retain and mode == "both" and _antigen_tags_untouched(cfg):
        return

    if mode and mode not in {"terminal", "exposed", "both"}:
        _add(errors, "invalid_specificity_epitope_mode", f"specificity.epitope_mode must be terminal/exposed/both: {mode}")
    if retain and retain not in {"N", "C"}:
        _add(errors, "invalid_specificity_cleavage_retain", f"specificity.cleavage.retain must be N or C: {retain}")

    _validate_antigen_construct_tags(cfg, errors, required=enabled or bool(mode) or bool(retain))

    if retain == "N" and _tag_present(cfg_get(cfg, "specificity.antigen_constructs.cleaved.c_tag")):
        _add(errors, "specificity_obstructing_tag", "N-retained antigen requires a free C terminus on the cleaved construct.")
    if retain == "C" and _tag_present(cfg_get(cfg, "specificity.antigen_constructs.cleaved.n_tag")):
        _add(errors, "specificity_obstructing_tag", "C-retained antigen requires a free N terminus on the cleaved construct.")

    if mode in {"terminal", "both"}:
        confirmed = cfg_get(cfg, "experiment.terminal_review_confirmed")
        if confirmed is not True:
            _add(
                errors,
                "terminal_review_not_confirmed",
                "Terminal/both epitope specificity requires experiment.terminal_review_confirmed=true.",
            )
        if retain not in {"N", "C"}:
            _add(errors, "missing_specificity_cleavage_retain", "Terminal epitope specificity requires specificity.cleavage.retain.")
    elif retain:
        _add(warnings, "cleavage_retain_without_terminal_mode", "specificity.cleavage.retain is set for a non-terminal epitope mode.")


def _validate_specificity_rows(
    sheet: pd.DataFrame,
    specificity_enabled: bool,
    approved_reviews: dict[tuple[str, str], dict[str, str]],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    terminal_review_confirmed: bool = False,
) -> None:
    columns = {
        "comparison_status",
        "specificity_complete",
        "terminal_modeling_status",
        "evidence_type",
        "review_status",
    }
    active = specificity_enabled or _has_specificity_values(sheet, columns)
    if not active:
        return
    missing = sorted(columns - set(sheet.columns))
    if missing:
        _add(errors, "missing_specificity_columns", f"Specificity-enabled handoff rows are missing columns: {', '.join(missing)}")
        return

    for index, row in sheet.iterrows():
        design_id = _row_id(row, index)
        actual_hash = _actual_sequence_sha256(row)
        declared_hash = str(row.get("sequence_sha256")) if "sequence_sha256" in row and not _is_blank(row.get("sequence_sha256")) else ""
        if not actual_hash:
            _add(errors, "missing_sequence_for_review", f"{design_id}: sequence is required to validate candidate review identity.", row=index)
        elif declared_hash != actual_hash:
            _add(errors, "sequence_hash_mismatch", f"{design_id}: sequence_sha256 does not match sequence.", row=index)
        review = approved_reviews.get((design_id, actual_hash)) if actual_hash else None
        review_matches = _review_matches(actual_hash, review)
        if specificity_enabled and not review_matches:
            _add(
                errors,
                "candidate_review_missing_or_stale",
                f"{design_id}: candidate_review.csv approval is missing or sequence_sha256 does not match synthesis row.",
                row=index,
            )
        if not _is_true_value(row.get("specificity_complete")):
            _add(errors, "specificity_incomplete", f"{design_id}: specificity_complete must be true.", row=index)

        comparison = _norm(row.get("comparison_status"))
        if comparison in _INCOMPLETE_STATUS:
            _add(errors, "specificity_unassessed", f"{design_id}: comparison_status is not assessed.", row=index)
        elif comparison in _BAD_SPECIFICITY_STATUS:
            _add(errors, "specificity_failed", f"{design_id}: comparison_status blocks handoff: {comparison}.", row=index)
        elif comparison != "complete":
            _add(errors, "invalid_specificity_comparison_status", f"{design_id}: comparison_status must be complete/cross_binding_risk/insufficient.", row=index)

        terminal = _norm(row.get("terminal_modeling_status"))
        if terminal == "unassessed":
            if terminal_review_confirmed and specificity_enabled and review_matches:
                # 신경망 예측은 새 말단의 화학적 인식을 평가할 수 없다. 연구자가 말단 검토를 명시적으로 확인
                # (experiment.terminal_review_confirmed)하고 서열 해시가 일치하는 후보별 승인이 있을 때만
                # 오류를 경고로 낮춘다. 둘 중 하나라도 없으면 그대로 차단한다.
                _add(warnings, "terminal_modeling_unassessed_human_reviewed",
                     f"{design_id}: 말단 모델링은 미평가이며 연구자 검토 확인으로 진행합니다. 말단 선택성은 실험으로 확인해야 합니다.",
                     row=index)
            else:
                _add(errors, "terminal_modeling_unassessed", f"{design_id}: terminal modeling is unassessed pending terminal review.", row=index)
        elif terminal in _INCOMPLETE_STATUS:
            _add(errors, "terminal_modeling_incomplete", f"{design_id}: terminal_modeling_status is incomplete.", row=index)
        elif terminal in _BAD_SPECIFICITY_STATUS:
            _add(errors, "specificity_obstructing_tag", f"{design_id}: terminal modeling reports obstructing tags.", row=index)
        elif terminal not in {"not_applicable", "complete", "clear", "pass", "passed"}:
            _add(errors, "invalid_terminal_modeling_status", f"{design_id}: terminal_modeling_status is not recognized.", row=index)

        evidence = _norm(row.get("evidence_type"))
        if evidence not in {"mock", "real"}:
            _add(errors, "invalid_specificity_evidence_type", f"{design_id}: evidence_type must be mock or real.", row=index)
        elif evidence == "mock":
            _add(errors, "mock_specificity_evidence", f"{design_id}: specificity evidence is mock, not experimental.", row=index)

        review = _norm(row.get("review_status"))
        if review in {"unassessed", "incomplete", "todo", "unknown", ""}:
            _add(errors, "specificity_review_unassessed", f"{design_id}: review_status is not assessed.", row=index)
        elif review == "pending":
            if specificity_enabled and review_matches:
                continue
            _add(errors, "specificity_review_pending", f"{design_id}: specificity review is pending without matched approved candidate_review.csv row.", row=index)
        elif review != "approved":
            _add(errors, "invalid_review_status", f"{design_id}: review_status must be approved or reviewed via candidate_review.csv.", row=index)


def _warn_draft_dna_label(cfg: dict, warnings: list[dict[str, Any]]) -> None:
    if cfg_get(cfg, "expression.codon_optimize") is True and cfg_get(cfg, "expression.codon_optimized_confirmed") is not True:
        _add(
            warnings,
            "draft_codon_substitution",
            "construct_dna is preferred-codon substitution draft DNA, not confirmed codon optimization.",
        )


def _warn_controls_in_synthesis(sheet: pd.DataFrame, warnings: list[dict[str, Any]]) -> None:
    for index, row in sheet.iterrows():
        markers = [
            _norm(row.get("design_id")),
            _norm(row.get("variant_id")),
            _norm(row.get("control_type")),
            _norm(row.get("notes")),
        ]
        if any(marker in {"wt", "control", "positive_control", "negative_control", "wt_control"} for marker in markers):
            _add(warnings, "control_in_synthesis_sheet", "Controls should be tracked in a separate controls table.", row=index)


def _validate_row_eligibility(sheet: pd.DataFrame, errors: list[dict[str, Any]]) -> None:
    if "design_id" not in sheet or sheet["design_id"].isna().any() or sheet["design_id"].duplicated().any():
        _add(errors, "invalid_candidate_ids", "synthesis rows need unique non-empty design_id values.")
    if "plate_id" in sheet:
        if sheet["plate_id"].map(_is_blank).any():
            _add(errors, "invalid_plate_ids", "Every synthesis row must name its plate_id.")
        if "plate_position" not in sheet:
            _add(errors, "missing_plate_position", "Named plates require plate_position in every synthesis row.")
        elif sheet.duplicated(["plate_id", "plate_position"]).any():
            _add(errors, "duplicate_plate_positions", "synthesis (plate_id, plate_position) pairs must be unique.")
    elif "plate_position" in sheet and sheet["plate_position"].duplicated().any():
        # Legacy one-plate sheets retain their original uniqueness contract.
        _add(errors, "duplicate_plate_positions", "synthesis plate positions must be unique.")
    for column, code in (("final_eligible", "not_final_eligible"), ("dev_cdr_evaluated", "developability_unassessed")):
        if column not in sheet.columns:
            _add(errors, f"missing_{column}_column", f"synthesis handoff requires {column}.")
            continue
        for index, row in sheet.iterrows():
            if not _is_true_value(row.get(column)):
                _add(errors, code, f"{_row_id(row, index)}: {column} must be true for handoff.", row=index)


def _antigen_tags_untouched(cfg: dict) -> bool:
    """항원 construct 태그가 기본값(전부 null=미확정, 또는 항목 없음)이면 True."""
    constructs = cfg_get(cfg, "specificity.antigen_constructs", {}) or {}
    if not isinstance(constructs, dict):
        return False
    for state in ("full", "cleaved"):
        node = constructs.get(state)
        if node is None:
            continue
        if not isinstance(node, dict):
            return False
        if any(node.get(tag_key) is not None for tag_key in ("n_tag", "c_tag")):
            return False
    return True


def _validate_antigen_construct_tags(cfg: dict, errors: list[dict[str, Any]], required: bool = False) -> None:
    constructs = cfg_get(cfg, "specificity.antigen_constructs", {}) or {}
    if not constructs:
        if required:
            _add(errors, "missing_antigen_constructs", "specificity.antigen_constructs must define full/cleaved n_tag and c_tag.")
        return
    for state in ("full", "cleaved"):
        node = constructs.get(state)
        if not isinstance(node, dict):
            _add(errors, "missing_antigen_construct", f"specificity.antigen_constructs.{state} must define n_tag and c_tag.")
            continue
        for tag_key in ("n_tag", "c_tag"):
            if tag_key not in node or node[tag_key] is None:
                _add(
                    errors,
                    "unknown_antigen_construct_tag",
                    f"specificity.antigen_constructs.{state}.{tag_key} must be explicit; use empty string only for confirmed free terminus.",
                )


def _has_specificity_values(sheet: pd.DataFrame, columns: set[str]) -> bool:
    present = [column for column in columns if column in sheet.columns]
    if not present or sheet.empty:
        return False
    return any(not _is_blank(value) for column in present for value in sheet[column].tolist())


def _validate_controls(sheet: pd.DataFrame, run_dir: Path, specificity_enabled: bool, errors: list[dict[str, Any]]) -> None:
    control_markers = []
    for index, row in sheet.iterrows():
        markers = [
            _norm(row.get("design_id")),
            _norm(row.get("variant_id")),
            _norm(row.get("control_type")),
            _norm(row.get("notes")),
        ]
        if any(marker in {"wt", "control", "positive_control", "negative_control", "wt_control"} for marker in markers):
            control_markers.append(index)
    for index in control_markers:
        _add(errors, "control_in_synthesis_sheet", "Controls must be tracked in a separate verified controls.csv.", row=index)

    if not specificity_enabled:
        return
    controls_path = run_dir / "05_final" / "controls.csv"
    if not controls_path.exists():
        _add(errors, "missing_controls_csv", "specificity handoff requires separate 05_final/controls.csv.")
        return
    try:
        controls = pd.read_csv(controls_path)
    except Exception as exc:  # noqa: BLE001
        _add(errors, "invalid_controls_csv", f"Could not read controls.csv: {exc}")
        return
    if controls.empty:
        _add(errors, "empty_controls_csv", "specificity controls.csv must define control rows.")
        return
    if "verified" not in controls.columns:
        _add(errors, "missing_controls_verified", "controls.csv must include verified column.")
        return
    if not {"control_id", "reagent_id"}.issubset(controls.columns):
        _add(errors, "missing_control_identity", "controls.csv needs control_id and reagent_id.")
        return
    missing = REQUIRED_CONTROLS - set(controls.control_id.dropna())
    if missing:
        _add(errors, "missing_control_conditions", f"Required control conditions missing: {', '.join(sorted(missing))}")
    for index, row in controls.iterrows():
        if _is_blank(row.get("reagent_id")):
            _add(errors, "missing_control_reagent", f"control row {index} needs reagent or reference-condition identity.", row=index)
        if not _is_true_value(row.get("verified")):
            _add(errors, "control_not_verified", f"control row {index} must have verified=true.", row=index)


def _validate_specificity_status(run_dir: Path, specificity_enabled: bool, errors: list[dict[str, Any]]) -> None:
    if not specificity_enabled:
        return
    status_path = run_dir / "06_specificity" / "status.json"
    if not status_path.exists():
        _add(errors, "missing_specificity_status", "specificity handoff requires 06_specificity/status.json.")
        return
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        _add(errors, "invalid_specificity_status", f"Could not read specificity status.json: {exc}")
        return
    if status.get("status") != "complete":
        _add(errors, "specificity_status_not_complete", f"specificity campaign status must be complete, found {status.get('status')!r}.")
    if status.get("biological_validation") != "not_performed":
        _add(errors, "invalid_biological_validation_status", "status.json biological_validation must be not_performed for computational handoff.")


def _load_candidate_reviews(
    run_dir: Path,
    specificity_enabled: bool,
    current_identities: set[tuple[str, str]],
    errors: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, str]]:
    if not specificity_enabled:
        return {}
    review_path = run_dir / "05_final" / "candidate_review.csv"
    if not review_path.exists():
        _add(errors, "missing_candidate_review", "specificity handoff requires 05_final/candidate_review.csv approvals.")
        return {}
    try:
        # index_col=False: 칸이 밀려 design_id 가 첫 열에서 사라진 채 승인으로 읽히지 않게 한다.
        reviews = pd.read_csv(review_path, index_col=False, dtype={"design_id": str, "sequence_sha256": str})
    except Exception as exc:  # noqa: BLE001
        _add(errors, "invalid_candidate_review", f"Could not read candidate_review.csv: {exc}")
        return {}
    required = {"design_id", "sequence_sha256", "review_status", "reviewer", "reviewed_at"}
    missing = sorted(required - set(reviews.columns))
    if missing:
        _add(errors, "missing_candidate_review_columns", f"candidate_review.csv is missing columns: {', '.join(missing)}")
        return {}
    if reviews.empty:
        _add(errors, "empty_candidate_review", "candidate_review.csv must include approved candidate rows.")
        return {}
    pair_keys = reviews[["design_id", "sequence_sha256"]].astype(str).apply(tuple, axis=1)
    if pair_keys.duplicated().any():
        _add(errors, "duplicate_candidate_review_identity", "candidate_review.csv has duplicate design_id+sequence_sha256 rows.")
    approved: dict[tuple[str, str], dict[str, str]] = {}
    for index, row in reviews.iterrows():
        design_id = str(row.get("design_id"))
        sequence_hash = "" if _is_blank(row.get("sequence_sha256")) else str(row.get("sequence_sha256"))
        identity = (design_id, sequence_hash)
        if identity not in current_identities:
            continue
        if _norm(row.get("review_status")) != "approved":
            _add(errors, "candidate_review_not_approved", f"{design_id}: candidate_review.csv review_status must be approved.", row=index)
            continue
        if _is_blank(row.get("reviewer")):
            _add(errors, "candidate_review_missing_reviewer", f"{design_id}: candidate_review.csv reviewer is required.", row=index)
        if _is_blank(row.get("reviewed_at")):
            _add(errors, "candidate_review_missing_reviewed_at", f"{design_id}: candidate_review.csv reviewed_at is required.", row=index)
        if not sequence_hash:
            _add(errors, "candidate_review_missing_sequence_hash", f"{design_id}: candidate_review.csv sequence_sha256 is required.", row=index)
        approved[identity] = {
            "sequence_sha256": sequence_hash,
            "reviewer": str(row.get("reviewer")),
            "reviewed_at": str(row.get("reviewed_at")),
        }
    return approved


def _current_review_identities(sheet: pd.DataFrame) -> set[tuple[str, str]]:
    identities: set[tuple[str, str]] = set()
    for index, row in sheet.iterrows():
        actual_hash = _actual_sequence_sha256(row)
        if actual_hash:
            identities.add((_row_id(row, index), actual_hash))
    return identities


def _review_matches(actual_hash: str, review: dict[str, str] | None) -> bool:
    if not review:
        return False
    return bool(actual_hash) and actual_hash == review.get("sequence_sha256")


def _actual_sequence_sha256(row: pd.Series) -> str:
    if "sequence" not in row or _is_blank(row.get("sequence")):
        return ""
    sequence = _clean_aa(row.get("sequence"))
    return hashlib.sha256(sequence.encode()).hexdigest()


def _clean_dna(value: Any) -> str:
    if _is_blank(value):
        return ""
    return "".join(ch for ch in str(value).upper() if not ch.isspace())


def _clean_aa(value: Any) -> str:
    if _is_blank(value):
        return ""
    return "".join(ch for ch in str(value).upper() if not ch.isspace())


def _row_id(row: pd.Series, index: int) -> str:
    for column in ("design_id", "variant_id", "plate_position"):
        if column in row and not _is_blank(row.get(column)):
            return str(row.get(column))
    return f"row {index}"


def _tag_present(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return not _is_blank(value) and _norm(value) not in {"false", "no", "none", "untagged", "free", "0"}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if _is_blank(value):
        return False
    return _norm(value) in {"true", "yes", "y", "1", "complete", "completed", "ok", "pass", "passed"}


def _is_true_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value is True
    if type(value).__name__ in {"bool_", "bool"} and type(value).__module__ == "numpy":
        return bool(value)
    return False


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip() == ""


def _norm(value: Any) -> str:
    if _is_blank(value):
        return ""
    return str(value).strip().lower()


def _add(items: list[dict[str, Any]], code: str, message: str, **extra: Any) -> None:
    item = {"code": code, "message": message}
    item.update(extra)
    items.append(item)
