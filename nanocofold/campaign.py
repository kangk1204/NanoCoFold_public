"""Sequential, resumable full/cleaved specificity campaigns using existing engines."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from . import design_rfantibody, developability, handoff, mature, rank_select, report, screen, specificity
from .config import PROJECT_ROOT, cfg_get, dump_resolved, resolve_path
from .utils import msa, seq, shell
from .utils.misc import to_native
from .utils.atomic import write_json, write_csv


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _key(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _save(path: Path, payload: dict) -> None:
    write_json(path, to_native(payload))


def _save_csv(frame: pd.DataFrame, path: Path) -> None:
    write_csv(path, frame)


def _read_library(path: Path) -> pd.DataFrame:
    """캐시된 후보 목록을 읽는다. 빈 칸이 있는 수치 열이 문자열로 굳어 순서가 바뀌는 것을 막는다."""
    frame = pd.read_csv(path, keep_default_na=False, index_col=False, dtype={"design_id": str})
    for column in ("esm_llr", "dev_severe"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _read_cache(path: Path) -> dict:
    try:
        value = _read(path)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


@contextmanager
def _run_lock(run_dir: Path):
    import fcntl

    with (run_dir / ".specificity.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("이 캠페인이 이미 실행 중입니다.") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def provenance(cfg: dict, definition: dict) -> dict:
    """Content identity includes dirty source, input files, environments and declared weights."""
    source = Path(__file__).parent
    result = {"source_sha256": _key({str(p.relative_to(source)): _hash(p) for p in sorted(source.rglob("*.py"))}),
              "inputs": {}, "environments": {}, "weights": {}}
    try:
        result["git_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
    except (OSError, subprocess.SubprocessError):
        result["git_head"] = "unavailable"
    # The normalized full sequence is already in the definition/cache key.
    for key in ("target.pdb", "specificity.cleaved_pdb", "screen.vhh_fasta", "mature.wt_fasta", "mature.wt_structure", "design.framework"):
        path = resolve_path(cfg, cfg_get(cfg, key))
        if path and path.is_file():
            result["inputs"][key] = _hash(path)
    for state in specificity.STATES:
        cached = resolve_path(cfg, cfg_get(cfg, "structure.msa.cache_dir", "data/msa_cache"))
        path = cached / f"{msa.sequence_hash(definition[f'{state}_sequence'])}.a3m" if cached else None
        result["inputs"][f"msa_{state}"] = _hash(path) if path and path.is_file() else None
    for key in ("screen.env", "screen.boltz2_env", "design.env", "mature.anarci.env", "mature.esm.env"):
        path = resolve_path(cfg, cfg_get(cfg, key))
        if path and path.exists():
            result["environments"][key] = _key({str(p.relative_to(path)): _hash(p) for p in sorted(path.glob("lib/python*/site-packages/*.dist-info/METADATA"))})
    for filename in cfg_get(cfg, "specificity.weight_files", []) or []:
        path = resolve_path(cfg, filename)
        if not path or not path.is_file():
            raise FileNotFoundError(f"가중치 provenance 파일이 없습니다: {filename}")
        result["weights"][str(path)] = _hash(path)
    return result


def _candidate_library(cfg: dict, run_dir: Path, target: dict) -> pd.DataFrame:
    mode = cfg_get(cfg, "run.mode")
    mock = bool(cfg_get(cfg, "run.mock"))
    if mode == "screen":
        path = resolve_path(cfg, cfg_get(cfg, "screen.vhh_fasta"))
        if not path or not path.is_file():
            raise ValueError("보유 후보 경로에는 screen.vhh_fasta / --vhh-fasta 가 필요합니다.")
        candidates = screen.fasta_to_candidates(path)
    elif mode == "design":
        generation_cfg = copy.deepcopy(cfg)
        generation_cfg["design"]["num_designs"] = min(int(cfg_get(cfg, "design.num_designs", 128)), 128)
        generation_cfg["design"]["mpnn"]["seqs_per_struct"] = min(int(cfg_get(cfg, "design.mpnn.seqs_per_struct", 2)), 2)
        candidates = design_rfantibody.run_design(generation_cfg, run_dir, target, mock=mock)["candidates"]
    elif mode == "mature":
        wt = mature.resolve_wt_sequence(cfg)
        if cfg_get(cfg, "mature.cdr.source") == "config":
            cdr = cfg_get(cfg, "mature.cdr.regions", {})
        elif not mock:
            cdr = mature.resolve_cdr_ranges_batch(cfg, {"WT": wt}, run_dir / "02_mature").get("WT", {})
        else:
            cdr = {}
        if not developability.sequence_liabilities(wt, cfg, cdr)["dev_cdr_evaluated"]:
            raise ValueError("WT CDR1/2/3 확인이 필요합니다. 자동 주석 실패를 임의 범위로 대체하지 않습니다.")
        lib = mature.generate_dms_library(wt, cdr, exclude_cys=bool(cfg_get(cfg, "mature.exclude.cys_substitution", True)),
                                         protect_wt_cys=bool(cfg_get(cfg, "mature.exclude.wt_cys", True)))
        pre_cfg = copy.deepcopy(cfg)
        pre_cfg["rank"]["include_wt_control"] = True
        candidates, _ = mature.run_prefilter(lib, wt, pre_cfg, run_dir / "02_mature", mock=mock)
        candidates = candidates.rename(columns={"variant_id": "design_id"})
    else:
        raise ValueError(mode)
    candidates = candidates[[c for c in ("design_id", "sequence", "source", "mutation", "esm_llr") if c in candidates]].copy()
    if candidates.empty:
        return pd.DataFrame(columns=["design_id", "sequence", "source", "sequence_sha256", "cdr3", "dev_cdr_evaluated", "dev_severe"])
    candidates["sequence"] = candidates.sequence.map(seq.clean_sequence)
    if candidates.design_id.duplicated().any() or candidates.sequence.eq("").any():
        raise ValueError("후보 ID 중복 또는 빈 서열입니다.")
    for candidate_id in candidates.design_id:
        seq.validate_candidate_id(str(candidate_id))
    candidates["sequence_sha256"] = candidates.sequence.map(lambda s: hashlib.sha256(s.encode()).hexdigest())
    duplicates = candidates[candidates.sequence.duplicated(keep="first")]
    _save_csv(duplicates, run_dir / "duplicate_candidates.csv")
    candidates = candidates.drop_duplicates("sequence").reset_index(drop=True)
    records = dict(zip(candidates.design_id.astype(str), candidates.sequence))
    if cfg_get(cfg, "mature.cdr.source") == "config":
        cdr_by_id = {cid: cfg_get(cfg, "mature.cdr.regions", {}) for cid in records}
    elif mock:
        cdr_by_id = {}
    else:
        cdr_by_id = mature.resolve_cdr_ranges_batch(cfg, records, run_dir / "04_developability")
    candidates = developability.triage(candidates, cfg, cdr_by_id=cdr_by_id)
    candidates["cdr3"] = [s[cdr_by_id[cid]["CDR3"][0]-1:cdr_by_id[cid]["CDR3"][1]]
                           if cid in cdr_by_id and "CDR3" in cdr_by_id[cid] else "" for cid, s in records.items()]
    candidates["source"] = candidates.get("source", pd.Series(mode, index=candidates.index))
    _save(run_dir / "cdr_annotations.json", cdr_by_id)
    return candidates


def _portable(path, root: Path):
    if not path:
        return ""
    try:
        return str(Path(path).resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _artifact_hashes(directory: Path, root: Path) -> dict:
    return {_portable(p, root): _hash(p) for p in sorted(directory.rglob("*"))
            if p.is_file() and p.suffix in {".cif", ".pdb", ".npz", ".json"} and p.name != "job.json"}


def _valid_record(record: dict, root: Path) -> bool:
    hashes = record.get("artifacts", {})
    if record.get("status") != "complete" or not hashes:
        return False
    try:
        return all(not Path(path).is_absolute() and (root / path).resolve().is_relative_to(root.resolve()) and
                   (root / path).is_file() and _hash(root / path) == value for path, value in hashes.items())
    except OSError:
        return False


def _matches_job(record: dict, candidate: pd.Series, state: str, seed: int, engine: str,
                 conditions: str, cfg: dict, root: Path) -> bool:
    expected = {"design_id": str(candidate.design_id), "sequence_sha256": hashlib.sha256(str(candidate.sequence).encode()).hexdigest(),
                "target_state": state, "seed": seed, "engine": engine, "restraint": "none",
                "comparison_conditions": conditions, "context": cfg_get(cfg, "specificity.context"),
                "evidence_type": "mock" if cfg_get(cfg, "run.mock") else "real"}
    return all(record.get(key) == value for key, value in expected.items()) and _valid_record(record, root)


def _prediction(cfg: dict, root: Path, directory: Path, target: dict, candidate: pd.DataFrame,
                state: str, seed: int, engine: str, conditions: str) -> dict:
    from .cli import _score_candidates

    job_cfg = copy.deepcopy(cfg)
    job_cfg["run"]["seed"] = seed
    job_cfg["score"].setdefault("boltz2", {})["seed"] = seed
    # Score the intended hotspot but never supply binding restraints to either form.
    job_cfg["screen"].setdefault("restraint", {})["enabled"] = False
    job_cfg["screen"]["allow_public_msa_server"] = False
    mock = bool(cfg_get(cfg, "run.mock"))
    record = {"design_id": str(candidate.iloc[0].design_id), "target_state": state, "seed": seed,
              "engine": engine, "restraint": "none", "comparison_conditions": conditions,
              "sequence_sha256": hashlib.sha256(str(candidate.iloc[0].sequence).encode()).hexdigest(),
              "context": cfg_get(cfg, "specificity.context"), "evidence_type": "mock" if mock else "real"}
    start = time.monotonic()
    try:
        predictions = screen.predict_complexes(job_cfg, directory / "predictions", target, candidate, engine=engine, mock=mock)
        metric = _score_candidates(job_cfg, predictions, target).iloc[0].to_dict()
        if metric.get("error"):
            raise ValueError(metric["error"])
        if not all(math.isfinite(float(metric.get(m, float("nan")))) for m in specificity.METRICS):
            raise ValueError("필수 계면 점수가 누락되었거나 유한하지 않습니다.")
        record.update(metric)
        from .utils import pdb_io
        structure = pdb_io.load_structure(record["cif_path"])
        chain = pdb_io.get_chain(structure, "A")
        binder = pdb_io.get_chain(structure, str(predictions.iloc[0].binder_chain))
        if pdb_io.chain_sequence(chain) != target["sequence"] or pdb_io.chain_sequence(binder) != str(candidate.iloc[0].sequence):
            raise ValueError("예측 구조 체인 서열이 입력과 다릅니다.")
        if metric.get("hotspot_mapping_failed"):
            raise ValueError("예측 구조의 hotspot 위치 대응 실패")
        # 체인 서열 검사(chain_sequence)와 같은 기준(표준 아미노산)으로 hotspot 을 인덱싱한다.
        residues = list(pdb_io.iter_residues(chain))
        binder_residues = list(pdb_io.iter_residues(binder))
        record["hotspot_contacts"] = [p for p in target["hotspot_seq_positions"]
            if min(pdb_io.residue_min_distance(residues[p - 1], r) for r in binder_residues) <= float(cfg_get(cfg, "score.contact_cutoff", 5.0))]
        terminal = residues[-1] if cfg_get(cfg, "specificity.cleavage.retain") == "N" else residues[0]
        record["terminal_atom_present"] = terminal.has_id("OXT" if cfg_get(cfg, "specificity.cleavage.retain") == "N" else "N")
        record["terminal_chemistry_validated"] = False
        record["cif_path"] = _portable(record.get("cif_path"), root)
        record["status"] = "complete"
        record["artifacts"] = _artifact_hashes(directory, root)
        record["msa_used"] = predictions.iloc[0].get("msa_used", "empty" if target.get("target_msa") == "empty" else "local")
        if engine == "chai1" and cfg_get(job_cfg, "screen.msa_mode") == "local" and record["msa_used"] != "chai1_local_msa":
            raise ValueError("MSA fallback으로 짝지은 비교 조건이 바뀌었습니다.")
    except (shell.ExecutionCancelled, TimeoutError, subprocess.TimeoutExpired) as exc:
        record.update(status="interrupted", error=f"{type(exc).__name__}: {exc}",
                      seconds=time.monotonic() - start)
        _save(directory / "job.json", record)
        raise
    except Exception as exc:
        record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        record["seconds"] = time.monotonic() - start
    _save(directory / "job.json", record)
    return record


def _target_evidence(root: Path, work: Path, definition: dict, targets: dict) -> None:
    """Record accessibility only where coordinates exist, with prediction caveats."""
    tables = {}
    for state in specificity.STATES:
        path = work / state / "01_target" / "hotspot_table.csv"
        tables[state] = pd.read_csv(path) if path.exists() and path.stat().st_size else pd.DataFrame()
    rows = []
    for full_pos, cleaved_pos in zip(definition["hotspots_full"], definition["hotspots_cleaved"]):
        row = {"full_position": full_pos, "cleaved_position": cleaved_pos,
               "interpretation": "구조 상태별 접근성 참고값; 절단 후 노출을 입증하지 않음"}
        for state, position in (("full", full_pos), ("cleaved", cleaved_pos)):
            table = tables[state]
            matches = table[table.seq_position.eq(position)] if "seq_position" in table else pd.DataFrame()
            value = matches.iloc[0].get("relative_sasa") if len(matches) == 1 else None
            # SASA 계산이 실패한 잔기(NaN)를 평가 완료로 표시하지 않는다.
            row[f"{state}_relative_sasa"] = value if pd.notna(value) else None
            row[f"{state}_structure_source"] = targets[state].get("qc", {}).get("provider", "unknown")
            row[f"{state}_assessment"] = "available" if row[f"{state}_relative_sasa"] is not None else "unassessed"
        rows.append(row)
    _save_csv(pd.DataFrame(rows), root / "06_specificity" / "surface_accessibility.csv")


def _diverse(frame: pd.DataFrame, n: int, primary_n: int = 16) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    # Actual annotated CDR3s, not a fixed tail window or an inferred motif.
    ordered = frame.sort_values(["cleaved_hotspot_contact_frac_min", "cleaved_ipsae_min", "full_ipsae_max", "design_id"],
                                ascending=[False, False, True, True], kind="stable")
    chosen = list(ordered.head(min(primary_n, n)).index)
    remaining = ordered.drop(index=chosen)
    while len(chosen) < n and not remaining.empty:
        distance = remaining.cdr3.map(lambda s: min(1 - rank_select.cdr3_identity(s, ordered.loc[i, "cdr3"]) for i in chosen) if chosen else 1)
        best = distance.idxmax()
        chosen.append(best)
        remaining = remaining.drop(index=best)
    return ordered.loc[chosen].copy()


def _initial_pool(frame: pd.DataFrame, n: int) -> pd.DataFrame:
    """Avoid selecting only an arbitrary FASTA prefix when a library exceeds the cap."""
    ordered = frame.sort_values(["dev_severe", "design_id"], kind="stable")
    if "esm_llr" in ordered:
        ordered = ordered.sort_values("esm_llr", ascending=False, kind="stable")
    chosen = []
    representatives = []
    deferred = []
    for index, row in ordered.iterrows():
        if any(rank_select.cdr3_identity(row.cdr3, r) >= .85 for r in representatives):
            deferred.append(index)
        else:
            chosen.append(index)
            representatives.append(row.cdr3)
    return ordered.loc[(chosen + deferred)[:n]]


def write_outputs(cfg: dict, root: Path, definition: dict, candidates: pd.DataFrame,
                  records: list[dict], run_status: str) -> dict:
    frame = pd.DataFrame(records)
    summary = specificity.aggregate(candidates, frame, cfg, definition["seeds"])
    if not summary.empty:
        summary["final_eligible"] = (summary.specificity_complete & summary.positive_quality_pass &
                                     summary.comparison_status.eq("complete") & summary.dev_cdr_evaluated &
                                     summary.dev_severe.le(float(cfg_get(cfg, "rank.max_dev_severe", 1))) &
                                     ~summary.design_id.eq("WT") & summary.evidence_type.eq("real"))
        summary["exclusion_reason"] = summary.apply(lambda r: ";".join([
            reason for condition, reason in ((not r.specificity_complete, "incomplete_comparison"),
                (not r.positive_quality_pass, "positive_quality"), (r.comparison_status == "cross_binding_risk", "cross_binding_risk"),
                (not r.dev_cdr_evaluated, "developability_unassessed"),
                (r.dev_severe > float(cfg_get(cfg, "rank.max_dev_severe", 1)), "developability"),
                (r.evidence_type == "mock", "mock"), (r.design_id == "WT", "wt_control")) if condition]), axis=1)
        for metric in specificity.METRICS:
            summary[metric] = summary[f"cleaved_{metric}"]
        if "cleaved_cif_path" in summary.columns:
            summary["cif_path"] = summary["cleaved_cif_path"].fillna("").astype(str)
        else:
            summary["cif_path"] = ""
    else:
        summary["final_eligible"] = pd.Series(dtype=bool)
    selected = _diverse(summary[summary.final_eligible], int(cfg_get(cfg, "specificity.final_n", 24)))
    selected["selection_rank"] = range(1, len(selected) + 1)
    selected["plate_position"] = [rank_select.plate_position(i) for i in range(len(selected))]
    for directory in ("03_score", "05_final", "06_specificity"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    # Commit authoritative observations before any derived export can fail.
    status = {"status": run_status, "n_candidates": len(candidates), "n_selected": len(selected),
              "n_failed_jobs": sum(r.get("status") == "failed" for r in records),
              "n_interrupted_jobs": sum(r.get("status") == "interrupted" for r in records),
              "biological_validation": "not_performed", "outputs_status": "pending",
              "report_status": "pending", "report": None}
    _save(root / "06_specificity" / "results.json", {"records": records, "candidates": candidates.to_dict("records"),
        "candidate_columns": list(candidates.columns), "definition": definition, "status": run_status})
    _save(root / "06_specificity" / "status.json", status)
    _save_csv(frame.drop(columns=["artifacts"], errors="ignore"), root / "06_specificity" / "observations.csv")
    _save_csv(summary, root / "03_score" / "scored_candidates.csv")
    _save_csv(summary, root / "06_specificity" / "comparison.csv")
    _save_csv(selected, root / "05_final" / "selected_candidates.csv")
    _save(root / "03_score" / "ranking_policy.json", {
        "method": "specificity_eligible_lexicographic_then_CDR3_diversity_v1",
        "sort_columns": ["cleaved_hotspot_contact_frac_min", "cleaved_ipsae_min", "full_ipsae_max", "design_id"],
        "sort_ascending": [False, False, True, True], "primary_n": 16,
        "normalization": "none; no composite z-score used",
        "selection": "eligible primary rows then farthest CDR3 identity additions",
        "cross_engine_scores_combined": False, "calibrated_binding_probability": False,
        "affinity_improvement_established": False,
    })
    seq.write_fasta(dict(zip(selected.design_id, selected.sequence)), root / "05_final" / "selected_candidates.fasta")
    synthesis = handoff.write_synthesis_sheet(selected, cfg, root / "05_final")
    warnings = ["계산 결과는 실험 우선순위입니다. 낮은 전장형 점수는 비결합 증거가 아닙니다.",
                "ipSAE 차이를 KD, 결합 확률 또는 선택성 배수로 해석하지 마세요.",
                "말단 원자/전하에 의한 특이성은 미평가입니다. 항원·construct·대조군 검토 후 발주하세요."]
    if cfg_get(cfg, "run.mock"):
        warnings.append("MOCK: 합성 예측입니다. 실제 후보 선별·발주에 사용할 수 없습니다.")
    if len(selected) < int(cfg_get(cfg, "specificity.final_n", 24)):
        warnings.append(f"요청 수보다 적은 {len(selected)}종이 계산 기준을 통과했습니다. 임계값은 완화하지 않았습니다.")
    # Never overwrite a scientist's completed experiment sheet on resume.
    results_file = root / "05_final" / "experiment_results.csv"
    if not results_file.exists():
        _save_csv(pd.DataFrame(columns=["design_id", "sequence_sha256", "target_state", "assay", "sample_batch", "replicate",
                              "concentration", "concentration_unit", "response", "kd", "kd_unit", "quantification_status",
                              "antigen_qc", "vhh_qc", "notes"]), results_file)
    controls_file = root / "05_final" / "controls.csv"
    if not controls_file.exists():
        _save_csv(pd.DataFrame([{"control_id": x, "reagent_id": "", "verified": False, "notes": "확정 필요"}
                      for x in ("full_antigen", "cleaved_antigen", "unrelated_vhh", "tag_control", "blank_reference")]), controls_file)
    review_file = root / "05_final" / "candidate_review.csv"
    readable = True
    if review_file.exists():
        try:
            # index_col=False: 연구자가 쉼표를 더 넣은 줄에서 열이 한 칸씩 밀려 design_id 가
            # 사라진 채 다시 저장되는 것을 막는다.
            reviews = pd.read_csv(review_file, keep_default_na=False, index_col=False,
                                  dtype={"design_id": str, "sequence_sha256": str})
        except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
            readable = False
            warnings.append(f"candidate_review.csv 를 읽을 수 없습니다. 파일을 보존하며 검토 목록을 갱신하지 않습니다: {exc}")
            reviews = pd.DataFrame()
    else:
        reviews = pd.DataFrame()
    # Append new identities; do not overwrite completed reviews or experiment results.
    reviewed = set(zip(reviews.get("design_id", []), reviews.get("sequence_sha256", [])))
    additions = [{"design_id": r.design_id, "sequence_sha256": r.get("sequence_sha256", ""),
                  "review_status": "pending", "reviewer": "", "reviewed_at": "", "notes": ""}
                 for _, r in selected.iterrows() if (r.design_id, r.get("sequence_sha256", "")) not in reviewed]
    if additions:
        reviews = pd.concat([reviews, pd.DataFrame(additions)], ignore_index=True)
    # 읽지 못한 검토표는 절대 덮어쓰지 않는다. 사람이 채운 내용이 유일본이다.
    if readable and (not review_file.exists() or additions):
        if reviews.empty:
            reviews = pd.DataFrame(columns=["design_id", "sequence_sha256", "review_status", "reviewer", "reviewed_at", "notes"])
        _save_csv(reviews, review_file)
    # The handoff gate is evaluated on every producing path, not only on the manual
    # `validate-handoff` command: a completed run must not look orderable while the
    # gate reports blocking errors. status.json must already hold this run's status,
    # otherwise the gate judges a previous (possibly successful) campaign.
    gate = handoff.validate_handoff(root)
    _save(root / "05_final" / "handoff_gate.json", {
        "ok": bool(gate.get("ok")), "errors": gate.get("errors", []), "warnings": gate.get("warnings", []),
    })
    gate_summary = {"ok": bool(gate.get("ok")), "n_errors": len(gate.get("errors", [])),
                    "n_warnings": len(gate.get("warnings", []))}
    if not gate_summary["ok"]:
        warnings.append(
            f"handoff 게이트 미통과({gate_summary['n_errors']}건): 발주 목록은 확정할 수 없습니다. "
            "`nanocofold validate-handoff` 로 항목을 확인하세요."
        )
    status["handoff_gate"] = gate_summary
    status["handoff_ready"] = bool(gate_summary["ok"])
    _save(root / "06_specificity" / "status.json", status)
    # 경고는 게이트 결과까지 확정한 뒤에 보고서로 넘긴다 (리포트는 호출 시점 값을 복사한다).
    context = {"num_candidates": len(candidates), "hotspots": definition["hotspots_full"],
               "specificity": True, "campaign_status": run_status, "warnings": warnings, "synthesis": synthesis, "handoff_gate": gate_summary}
    status.update(outputs_status="complete", report_status="pending", report=None)
    _save(root / "06_specificity" / "status.json", status)
    try:
        report_path = report.generate_report(root, cfg, context, summary, selected)
    except Exception as exc:
        status.update(report_status="failed", report_error=f"{type(exc).__name__}: {exc}")
        _save(root / "06_specificity" / "status.json", status)
        raise
    status.update(report_status="complete", report=str(report_path))
    _save(root / "06_specificity" / "status.json", status)
    return status


def reanalyze(cfg: dict, root: Path) -> dict:
    """Rebuild selection/report without engines, preserving the original target identity."""
    payload = _read(root / "06_specificity" / "results.json")
    if not payload:
        raise ValueError("특이성 재분석 자료가 없습니다.")
    current = _read(root / "06_specificity" / "status.json")
    if current.get("status") in {None, "running", "failed"}:
        raise ValueError("최근 캠페인이 실행 중이거나 실패했습니다. 이전 결과를 승격하지 말고 캠페인을 재개하세요.")
    from .config import load_config
    stored = load_config(root / "run_config.yaml")
    # Rescoring can change scientific quality thresholds/selection counts only.
    for key in ("run.mock", "screen", "structure", "mature", "developability", "specificity.engine",
                "specificity.secondary_chai"):
        if cfg_get(cfg, key) != cfg_get(stored, key):
            raise ValueError(f"재분석에서 예측/서열 조건을 변경할 수 없습니다: {key}")
    if specificity.resolve_definition(cfg) != payload["definition"]:
        raise ValueError("타깃 정의가 변경되었습니다. 새 캠페인이 필요합니다.")
    records = payload["records"]
    for record in records:
        if record.get("status") == "complete" and not _valid_record(record, root):
            record.update(status="failed", error="예측 산출물 누락/손상. 실제 캠페인을 재개하세요.")
    status = "partial_failure" if any(r["status"] == "failed" for r in records) else payload["status"]
    candidates = pd.DataFrame(payload["candidates"], columns=payload.get("candidate_columns"))
    return write_outputs(cfg, root, payload["definition"], candidates, records, status)


def run_campaign(cfg: dict, root: Path, dry_run: bool = False) -> dict:
    definition = specificity.resolve_definition(cfg)
    mapping = specificity.residue_mapping(cfg, definition)
    if dry_run:
        return {"status": "dry_run", "definition": definition, "max_primary_jobs":
                2 * int(cfg_get(cfg, "specificity.stage1_n", 48)) +
                2 * int(cfg_get(cfg, "specificity.stage2_n", 32)) * (len(definition["seeds"]) - 1)}
    root.mkdir(parents=True, exist_ok=True)
    with _run_lock(root):
        _save(root / "06_specificity" / "status.json", {"status": "running", "biological_validation": "not_performed"})
        try:
            return _run_locked(cfg, root, definition, mapping)
        except BaseException as exc:
            status = _read(root / "06_specificity" / "status.json") or {}
            if isinstance(exc, (shell.ExecutionCancelled, KeyboardInterrupt)):
                status["status"] = "cancelled"
            elif isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)):
                status["status"] = "budget_exhausted"
            elif status.get("outputs_status") == "pending":
                # Calculation is preserved; an export error must not erase it.
                status["outputs_status"] = "failed"
            elif status.get("outputs_status") != "complete":
                status["status"] = "failed"
            status.update(error=f"{type(exc).__name__}: {exc}", biological_validation="not_performed")
            _save(root / "06_specificity" / "status.json", status)
            raise


def _run_locked(cfg: dict, root: Path, definition: dict, mapping: pd.DataFrame) -> dict:
    from . import diagnostics

    mock = bool(cfg_get(cfg, "run.mock"))
    if not mock:
        diagnostic = diagnostics.doctor(cfg)
        _save(root / "diagnostics.json", diagnostic)
        if not diagnostic["ok"]:
            raise RuntimeError("필수 실행 환경 점검 실패. diagnostics.json 을 확인하세요.")
    identity = provenance(cfg, definition)
    if not mock:
        # Runtime evidence may include timings; cache identity uses stable fields only.
        runtime = diagnostics.runtime_provenance(cfg)
        _save(root / "runtime_provenance.json", runtime)
        identity["runtime"] = {name: {"weights": value.get("weights"),
                                      "imports": value.get("imports", {}).get("imports", {})}
                               for name, value in runtime.get("engines", {}).items()}
    canonical = copy.deepcopy(cfg)
    canonical["target"].update(sequence=definition["full_sequence"], fasta=None)
    canonical["run"].pop("outdir", None)
    canonical["specificity"].pop("budget_hours", None)
    fingerprint = _key({"config": canonical, "definition": definition, "provenance": identity})
    work = root / "06_specificity" / fingerprint
    work.mkdir(parents=True, exist_ok=True)
    _save(work / "provenance.json", identity)
    _save(root / "06_specificity" / "definition.json", definition)
    _save_csv(mapping, root / "06_specificity" / "residue_mapping.csv")
    resolved_cfg = copy.deepcopy(cfg)
    resolved_cfg["target"].update(sequence=definition["full_sequence"], fasta=None)
    dump_resolved(resolved_cfg, root / "run_config.yaml")
    ledger_path = root / "06_specificity" / "budget.json"
    ledger = _read(ledger_path) or {"seconds": 0.0}
    if ledger.get("started_at"):
        ledger["seconds"] += max(0, time.time() - ledger["started_at"])
    remaining = float(cfg_get(cfg, "specificity.budget_hours", 48)) * 3600 - ledger["seconds"]
    ledger["started_at"] = time.time()
    _save(ledger_path, ledger)
    start = time.monotonic()
    candidates = pd.DataFrame(columns=["design_id", "sequence", "cdr3", "dev_cdr_evaluated", "dev_severe"])
    records = []
    run_status = "complete"
    unevaluable = False
    interrupted = None
    library = work / "candidates.csv"
    if library.exists() and _read_cache(work / "candidates_manifest.json").get("sha256") == _hash(library):
        candidates = _read_library(library)
    try:
        with shell.execution_budget(remaining):
            if remaining <= 0:
                raise TimeoutError("누적 계산 시간 한도 도달")
            from .cli import _target_phase
            from .state import RunState
            targets = {}
            for state in specificity.STATES:
                scfg = specificity.state_config(cfg, definition, state)
                targets[state] = _target_phase(scfg, work / state, RunState(work / state), mock, False)
            _target_evidence(root, work, definition, targets)
            if (targets["full"]["target_msa"] == "empty") != (targets["cleaved"]["target_msa"] == "empty"):
                raise ValueError("양쪽 상태의 MSA 정책이 다릅니다. 둘 다 로컬 MSA 또는 둘 다 empty로 준비하세요.")
            if library.exists() and _read_cache(work / "candidates_manifest.json").get("sha256") == _hash(library):
                candidates = _read_library(library)
            else:
                candidates = _candidate_library(cfg, work, targets["cleaved"])
                _save_csv(candidates, library)
                _save(work / "candidates_manifest.json", {"sha256": _hash(library)})
            # 후보가 없거나 CDR 주석이 한 건도 없으면 입력/도구 문제다. 후보는 평가됐지만 기준에 모두 걸린
            # 정상적인 0건과 구별한다(docs/22: 적격 후보가 0개인 정상 실행과 실행 실패를 구별).
            unevaluable = bool(candidates.empty or not candidates.dev_cdr_evaluated.astype(bool).any())
            eligible = candidates[candidates.dev_cdr_evaluated & candidates.dev_severe.le(float(cfg_get(cfg, "rank.max_dev_severe", 1)))].copy()
            pool = _initial_pool(eligible, int(cfg_get(cfg, "specificity.stage1_n", 48)))
            if cfg_get(cfg, "run.mode") == "mature" and "WT" in candidates.design_id.values and "WT" not in pool.design_id.values:
                pool = pd.concat([pool, candidates[candidates.design_id.eq("WT")]], ignore_index=True)
            engine = cfg_get(cfg, "specificity.engine", "boltz2")
            conditions = _key({"engine": engine, "restraint": "none", "context": definition["context"],
                               "screen": cfg_get(cfg, "screen"), "score": cfg_get(cfg, "score"), "provenance": identity})
            for seed_index, seed in enumerate(definition["seeds"]):
                for _, row in pool.iterrows():
                    for state in specificity.STATES:
                        directory = work / "jobs" / engine / str(seed) / state / str(row.design_id)
                        previous = _read_cache(directory / "job.json")
                        if previous and _matches_job(previous, row, state, seed, engine, conditions, cfg, root) and cfg_get(cfg, "run.resume", True):
                            records.append(previous)
                            continue
                        if time.monotonic() - start >= remaining:
                            raise TimeoutError("누적 계산 시간 한도 도달")
                        record = _prediction(cfg, root, directory, targets[state], row.to_frame().T, state, seed, engine, conditions)
                        records.append(record)
                        print(f"[specificity] {row.design_id} {state} seed={seed}: {record['status']}")
                if seed_index == 0:
                    initial = specificity.aggregate(pool, pd.DataFrame(records), cfg, [seed])
                    qualifying = initial[initial.specificity_complete & initial.positive_quality_pass & initial.comparison_status.eq("complete")]
                    selected = _diverse(qualifying, int(cfg_get(cfg, "specificity.stage2_n", 32)))
                    ids = set(selected.design_id)
                    if cfg_get(cfg, "run.mode") == "mature":
                        ids.add("WT")
                    pool = pool[pool.design_id.isin(ids)]
            if cfg_get(cfg, "specificity.secondary_chai", False) and engine != "chai1":
                secondary_cfg = copy.deepcopy(cfg)
                secondary_cfg["run"]["mode"] = "screen"
                secondary_cfg["structure"]["provider"] = "none"
                secondary_cfg["specificity"]["engine"] = "chai1"
                if not mock:
                    secondary_diagnostic = diagnostics.doctor(secondary_cfg, smoke=True)
                    _save(root / "secondary_diagnostics.json", secondary_diagnostic)
                    if not secondary_diagnostic["ok"]:
                        raise RuntimeError("보조 Chai-1 실제 smoke 검증 실패")
                primary = specificity.aggregate(pool, pd.DataFrame(records), cfg, definition["seeds"])
                top = _diverse(primary[primary.specificity_complete & primary.positive_quality_pass], 8)
                for _, row in pool[pool.design_id.isin(top.design_id)].iterrows():
                    for state in specificity.STATES:
                        directory = work / "jobs" / "chai1" / str(definition["seeds"][0]) / state / str(row.design_id)
                        previous = _read_cache(directory / "job.json")
                        secondary_conditions = _key({"primary": conditions, "engine": "chai1"})
                        if previous and _matches_job(previous, row, state, definition["seeds"][0], "chai1", secondary_conditions, cfg, root) and cfg_get(cfg, "run.resume", True):
                            records.append(previous)
                        else:
                            if time.monotonic() - start >= remaining:
                                raise TimeoutError("누적 계산 시간 한도 도달")
                            records.append(_prediction(secondary_cfg, root, directory, targets[state], row.to_frame().T,
                                                       state, definition["seeds"][0], "chai1", secondary_conditions))
            if any(r.get("status") == "failed" for r in records):
                run_status = "partial_failure"
            elif not records and unevaluable:
                # _run_locked 은 dry_run 이 run_campaign 에서 이미 걸러졌으므로, 평가 가능한 후보가
                # 하나도 없어 계산이 0건인 것은 정상 완료가 아니라 입력/도구 실패다.
                run_status = "no_predictions"
    except (TimeoutError, subprocess.TimeoutExpired, shell.ExecutionCancelled, KeyboardInterrupt) as exc:
        interrupted = exc
        run_status = "cancelled" if isinstance(exc, (shell.ExecutionCancelled, KeyboardInterrupt)) else "budget_exhausted"
        # Interrupted/previous work remains visible even if the new budget is zero.
        records = []
        for path in sorted((work / "jobs").rglob("job.json")):
            record = _read_cache(path)
            if not record:
                continue
            if record.get("status") != "interrupted" and not _valid_record(record, root):
                record.update(status="failed", error="불완전하거나 손상된 캐시")
            records.append(record)
    finally:
        ledger["seconds"] += time.monotonic() - start
        ledger["started_at"] = None
        _save(ledger_path, ledger)
    status = write_outputs(cfg, root, definition, candidates, records, run_status)
    status["fingerprint"] = fingerprint
    if interrupted is not None and isinstance(interrupted, (shell.ExecutionCancelled, KeyboardInterrupt)):
        raise interrupted
    return status
