from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import mock as mock_mod
from . import provenance
from .config import cfg_get, resolve_path
from .utils import pdb_io, shell

# Public tutorial sequence, used only by the synthetic CPU design path.
# Source: examples/tutorial/sources.json (PDB 9VQ6, anti-IL6 nanobody).
FALLBACK_FRAMEWORK_SEQ = (
    'MAQVQLVESGGGLVQPGGSLRLSCAASGFTFSSYRMYWFRQAPGQGLEAVAAISAGGGSTYYADSVKGRFTISRDNSKNTLYLQMNSLRAEDTAVYYCANRAGWGMGDYWGQGTLVTVSKLAAALEHHHHHH'
)


def _log_path(phase_dir: Path, filename: str, dry_run: bool) -> Path:
    path = phase_dir / filename
    return path.with_name(f"{path.stem}.dry-run{path.suffix}") if dry_run else path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_stage(
    env,
    argv: list,
    *,
    name: str,
    phase_dir: Path,
    inputs: list[Path],
    output: Path,
    env_vars: dict,
    log_path: Path,
    resume: bool,
    dry_run: bool,
) -> bool:
    """RFantibody 의 무거운 단계 하나를 실행하거나, 검증된 이전 산출물을 재사용한다.

    수 시간짜리 GPU 단계가 끝난 뒤 뒤쪽 단계(점수 추출 등)에서 실패해도 앞 단계를 다시 계산하지 않도록,
    단계가 끝난 뒤 (인자, 입력 파일 해시, env_vars, 산출물 해시)를 marker 로 남긴다.
    재사용 조건은 모두 일치할 때뿐이며 하나라도 다르면 처음부터 다시 실행한다. 반환값은 재사용 여부.
    설치된 엔진 소스·모델 가중치·유효 환경도 fingerprint 에 포함한다.
    """
    marker = phase_dir / f".stage_{name}.json"
    fingerprint = None
    if not dry_run:
        fingerprint = hashlib.sha256(json.dumps({
            "argv": [str(a) for a in argv],
            "inputs": {str(p): _sha256_file(p) if Path(p).exists() else None for p in inputs},
            "env_vars": {str(k): str(v) for k, v in (env_vars or {}).items()},
            "runtime": provenance.runtime_identity({}, "rfantibody", env=env, argv=argv, env_vars=env_vars),
        }, sort_keys=True).encode()).hexdigest()
        if resume and marker.exists() and output.exists() and output.stat().st_size > 0:
            try:
                stored = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                stored = {}
            if (stored.get("fingerprint") == fingerprint
                    and stored.get("output_sha256") == _sha256_file(output)):
                print(f"[design] {name}: 입력·인자·산출물 해시가 일치해 이전 결과를 재사용합니다.")
                return True
        # 중단·이전 산출물이 새 결과에 섞이거나(quiver 는 이어 쓰기) 낡은 marker 로 오인되지 않게 지운다.
        sidecars = []
        if name.startswith("rfdiffusion_"):
            # RFdiffusion writes these separate quivers even with the same -q
            # output. Existing trajectory tags otherwise abort a valid rerun.
            sidecars = [output.with_name(output.stem + suffix) for suffix in
                        ("_Xt-1_traj.qv", "_pX0_traj.qv")]
        provenance.archive_existing([marker, output, log_path, *sidecars], phase_dir)
    shell.run_in_env(env, argv, extra_env=env_vars, log_path=log_path, dry_run=dry_run)
    if not dry_run:
        if not output.is_file() or output.stat().st_size == 0:
            raise shell.ToolError(f"RFantibody {name} exited without its required nonempty quiver output: {output.name}")
        marker.write_text(json.dumps({
            "stage": name, "fingerprint": fingerprint, "output": output.name,
            "output_sha256": _sha256_file(output),
        }, indent=2) + "\n", encoding="utf-8")
    return False


def framework_sequence(framework_path: Path | None) -> str:
    if framework_path and Path(framework_path).exists():
        structure = pdb_io.load_structure(framework_path)
        model = next(iter(structure.get_models()))
        for chain in model:
            sequence = pdb_io.chain_sequence(chain)
            if sequence:
                return sequence
    return FALLBACK_FRAMEWORK_SEQ


def concatenate_quivers(quivers: list[Path], destination: Path, dry_run: bool = False) -> Path:
    """청크별 RFdiffusion quiver를 하나로 병합한다 (RFantibody README의 cat 방식).

    병합하지 않으면 ProteinMPNN이 첫 청크만 처리해 나머지 백본이 조용히 폐기된다.
    """
    if dry_run:
        return destination
    with destination.open("wb") as handle:
        for quiver in quivers:
            if quiver.exists() and quiver.stat().st_size > 0:
                handle.write(quiver.read_bytes())
    return destination


def format_hotspots_for_rfdiffusion(labels: list[str]) -> str:
    return ",".join(label.replace(":", "") for label in labels)


def run_design(cfg: dict, run_dir: Path, target_info: dict, mock: bool = False, dry_run: bool = False) -> dict:
    """RFantibody (RFdiffusion_Ab → ProteinMPNN → RF2) 설계 캠페인."""
    from .input_review import validate_reviewed_input
    validate_reviewed_input(cfg, design=True)
    phase_dir = run_dir / "02_design"
    phase_dir.mkdir(parents=True, exist_ok=True)

    num_designs = int(cfg_get(cfg, "design.num_designs", 100))
    framework = resolve_path(cfg, cfg_get(cfg, "design.framework"))
    engine = str(cfg_get(cfg, "design.engine", "rfantibody")).lower()
    if engine != "rfantibody" and not mock:
        raise ValueError(f"design.engine='{engine}' 은 지원하지 않습니다 (지원: rfantibody).")
    hotspots = target_info.get("hotspot_pdb_labels") or target_info.get("hotspot_labels", [])
    if not target_info.get("structure"):
        raise ValueError(
            "design 모드에는 타깃 3D 구조가 필요합니다. target.pdb 를 지정하거나 "
            "structure.provider=boltz2/chai1 로 구조를 예측하세요 (screen/mature 모드는 서열만으로 가능)."
        )

    all_candidates = None
    if mock and dry_run:
        candidates = pd.DataFrame(columns=["design_id", "sequence", "source"])
        info = {"engine": "mock", "dry_run": True, "num_requested": num_designs, "filters": {"before": 0, "after": 0}}
    elif mock:
        candidates, info = _mock_design(cfg, phase_dir, target_info, framework)
        all_candidates = candidates.copy()
        candidates, filter_info = apply_design_filters(candidates, cfg)
        info["filters"] = filter_info
        selected = set(candidates["design_id"])
        all_candidates["tier2_requested"] = all_candidates["design_id"].isin(selected)
        all_candidates["design_screening_reason"] = all_candidates["tier2_requested"].map(
            {True: None, False: "RF2 mock 사전 필터로 재예측하지 않았습니다."})
        all_candidates["structure_stage"] = "mock"
        all_candidates["cif_path"] = all_candidates["pred_dir"].map(
            lambda directory: str(next(Path(directory).glob("pred.model_idx_*.cif"))))
        all_candidates.to_csv(phase_dir / "all_generated_candidates.csv", index=False)
    else:
        candidates, info = _run_rfantibody(cfg, phase_dir, target_info, framework, hotspots, num_designs, dry_run)

    if not dry_run:
        candidates.to_csv(phase_dir / "design_candidates.csv", index=False)
    info["num_candidates"] = len(candidates)
    info.setdefault("requested_run_seed", cfg_get(cfg, "run.seed"))
    info.setdefault("seed_control", "not_executed_mock" if mock else "no_explicit_seed_argument_in_pinned_stage_clis")
    info.setdefault("stage_randomness_control", {
        "rfdiffusion": {"explicit_seed_argument": False, "deterministic_toggle": bool(cfg_get(cfg, "design.deterministic", False))},
        "proteinmpnn": {"explicit_seed_argument": False},
        "rf2": {"explicit_seed_argument": False},
    })
    info.setdefault("paired_random_state_established", False)
    info["execution_mode"] = "mock_dry_run" if mock and dry_run else "mock" if mock else "dry_run" if dry_run else "real"
    result = {"candidates": candidates, "info": info, "phase_dir": phase_dir}
    if all_candidates is not None:
        result["all_candidates"] = all_candidates
    return result

def _run_rfantibody(
    cfg: dict,
    phase_dir: Path,
    target_info: dict,
    framework: Path | None,
    hotspots: list[str],
    num_designs: int,
    dry_run: bool,
) -> tuple[pd.DataFrame, dict]:
    env = resolve_path(cfg, cfg_get(cfg, "design.env"))
    if (framework is None or not Path(framework).exists()) and not dry_run:
        raise FileNotFoundError(
            f"RFantibody 프레임워크 PDB가 없습니다: {framework}\n"
            "envs/setup_rfantibody.sh 를 실행하면 data/frameworks/h-NbBCII10.pdb 가 생성됩니다."
        )
    if framework is None:
        framework = Path("data/frameworks/h-NbBCII10.pdb")
    if not dry_run:
        provenance.archive_existing([phase_dir / "all_generated_candidates.csv", phase_dir / "design_candidates.csv",
                                     phase_dir / "generation_failure.json"], phase_dir)
    target_pdb = Path(target_info["structure"])
    from .rfantibody_context import prepare_context, restore_context
    context = prepare_context(target_info, phase_dir, dry_run=dry_run)
    if context is not None:
        target_pdb = Path(context["structure"])
        hotspots = context["hotspots"]
    loops = str(cfg_get(cfg, "design.loops", "H1:7,H2:6,H3:5-13"))
    chunk_size = max(1, int(cfg_get(cfg, "design.chunk_size", 200)))
    seqs_per_struct = int(cfg_get(cfg, "design.mpnn.seqs_per_struct", 4))
    temperature = float(cfg_get(cfg, "design.mpnn.temperature", 0.2))
    recycles = int(cfg_get(cfg, "design.rf2.num_recycles", 10))
    hotspot_arg = format_hotspots_for_rfdiffusion(hotspots)
    env_vars = cfg_get(cfg, "design.env_vars", {}) or {}

    rfdiffusion = shell.env_executable(env, "rfdiffusion", dry_run=dry_run)
    proteinmpnn = shell.env_executable(env, "proteinmpnn", dry_run=dry_run)
    rf2 = shell.env_executable(env, "rf2", dry_run=dry_run)
    qvscorefile = shell.env_executable(env, "qvscorefile", dry_run=dry_run)
    qvextractspecific = shell.env_executable(env, "qvextractspecific", dry_run=dry_run)

    backbone_quivers: list[Path] = []
    resume = bool(cfg_get(cfg, "run.resume", True))
    resumed_stages: list[str] = []
    remaining = num_designs
    chunk_index = 0
    extra_overrides = [str(a) for a in (cfg_get(cfg, "design.extra_args", []) or [])]
    deterministic = bool(cfg_get(cfg, "design.deterministic", False))
    while remaining > 0:
        chunk = min(chunk_size, remaining)
        chunk_index += 1
        qv = phase_dir / f"backbones_{chunk_index:03d}.qv"
        argv = [
            str(rfdiffusion),
            "-t", str(target_pdb),
            "-f", str(framework),
            "-q", str(qv),
            "-n", str(chunk),
            "-l", loops,
        ]
        if hotspots:
            argv.extend(["-h", hotspot_arg])
        if deterministic:
            argv.append("--deterministic")
        for override in extra_overrides:
            argv.extend(["-e", override])
        stage_name = f"rfdiffusion_{chunk_index:03d}"
        if _run_stage(
            env, argv, name=stage_name, phase_dir=phase_dir, inputs=[target_pdb, Path(framework)], output=qv,
            env_vars=env_vars, log_path=_log_path(phase_dir, f"{stage_name}.log", dry_run),
            resume=resume, dry_run=dry_run,
        ):
            resumed_stages.append(stage_name)
        backbone_quivers.append(qv)
        remaining -= chunk

    design_quiver = phase_dir / "designs_mpnn.qv"
    predictions_quiver = phase_dir / "designs_rf2.qv"
    combined_quiver = concatenate_quivers(backbone_quivers, phase_dir / "backbones_all.qv", dry_run=dry_run)
    if _run_stage(
        env,
        [str(proteinmpnn), "-q", str(combined_quiver), "--output-quiver", str(design_quiver),
         "-n", str(seqs_per_struct), "-t", str(temperature)],
        name="proteinmpnn", phase_dir=phase_dir, inputs=[combined_quiver], output=design_quiver,
        env_vars=env_vars, log_path=_log_path(phase_dir, "proteinmpnn.log", dry_run),
        resume=resume, dry_run=dry_run,
    ):
        resumed_stages.append("proteinmpnn")
    if _run_stage(
        env,
        [str(rf2), "-q", str(design_quiver), "--output-quiver", str(predictions_quiver), "-r", str(recycles)],
        name="rf2", phase_dir=phase_dir, inputs=[design_quiver], output=predictions_quiver,
        env_vars=env_vars, log_path=_log_path(phase_dir, "rf2.log", dry_run),
        resume=resume, dry_run=dry_run,
    ):
        resumed_stages.append("rf2")
    score_path = predictions_quiver.with_suffix(".sc")
    qvscore_warnings: list[str] = []
    if not dry_run:
        # Quiver stages have their own content-verified reuse markers. These
        # derived outputs are regenerated below and must never survive as the
        # current attempt if qvscorefile/extraction omit a file or fail.
        provenance.archive_existing([score_path, phase_dir / "qvscorefile.log",
                                     phase_dir / "qvextract.log", phase_dir / "rf2_pdbs",
                                     phase_dir / "rf2_context_pdbs"], phase_dir)
        shell.run_in_env(
            env,
            [str(qvscorefile), str(predictions_quiver)],
            extra_env=env_vars,
            log_path=phase_dir / "qvscorefile.log",
        )
        if not score_path.is_file() or score_path.stat().st_size == 0:
            raise shell.ToolError(f"RFantibody qvscorefile did not produce required nonempty output {score_path.name}; see qvscorefile.log")

    # A plan must not consume stale real scores or rewrite restored outputs.
    generated_scores = _parse_rf2_scorefile(score_path) if not dry_run and score_path.exists() else pd.DataFrame()
    if not dry_run and generated_scores.empty:
        raise shell.ToolError(f"RFantibody qvscorefile output contains no valid generated candidates: {score_path.name}")
    scores, filter_info = apply_design_filters(generated_scores, cfg)
    top_n = int(cfg_get(cfg, "design.filters.top_n_to_score", 300))
    if not scores.empty:
        scores = scores.sort_values("rf2_pae", ascending=True).head(top_n)
    tags = list(scores["design_id"]) if not scores.empty else []
    selected_tags = set(tags)
    # Full generation and expensive final prediction are separate decisions.
    # Preserve every RF2 candidate for researcher exploration and exports.
    all_tags = list(generated_scores["design_id"]) if not generated_scores.empty else []

    extract_dir = phase_dir / "rf2_pdbs"
    extraction_warnings: list[str] = []
    if not dry_run and all_tags:
        listing = "\n".join(all_tags) + "\n"
        result = shell.run_in_env(
            env,
            [str(qvextractspecific), str(predictions_quiver), "-o", str(extract_dir)],
            extra_env=env_vars,
            log_path=phase_dir / "qvextract.log",
            check=False,
            stdin_text=listing,
        )
        if result.returncode != 0:
            extraction_warnings.append(f"qvextractspecific 실패(exit={result.returncode}) — 로그 확인")
        elif not any(extract_dir.glob("*.pdb")):
            extraction_warnings.append("추출된 PDB가 없습니다(qv tag/경로 확인).")

    rows: list[dict] = []
    for _, row in generated_scores.iterrows():
        design_id = str(row["design_id"])
        pdb_path = extract_dir / f"{design_id}.pdb"
        sequence = str(row.get("sequence") or "")
        target_chain, binder_chain = "T", "H"
        restored = None
        if context is not None and pdb_path.exists():
            # Native HLT T is a documented aggregate representation, not the
            # sequence of our single selected primary target. Restore only from
            # the exact input manifest, retaining the original RF output.
            try:
                restored = restore_context(
                    pdb_path, phase_dir / "rf2_context_pdbs" / f"{design_id}.pdb", context
                )
            except Exception as exc:  # noqa: BLE001
                extraction_warnings.append(f"{design_id}: multichain context 복원 실패 ({exc})")
                restored = None
            if restored:
                pdb_path = Path(restored["pred_dir"])
                target_chain, binder_chain = restored["target_chain"], restored["binder_chain"]
        if pdb_path.exists():
            try:
                structure = pdb_io.load_structure(pdb_path)
                chain_ids = pdb_io.chain_ids(structure)
                if context is None:
                    target_chain = "T" if "T" in chain_ids else chain_ids[-1]
                    binder_chain = "H" if "H" in chain_ids else chain_ids[0]
                binder_seq = pdb_io.chain_sequence(pdb_io.get_chain(structure, binder_chain))
                if binder_seq:
                    sequence = binder_seq
                else:
                    extraction_warnings.append(f"{design_id}: extracted binder chain has no protein sequence")
            except Exception as exc:  # noqa: BLE001
                extraction_warnings.append(f"{design_id}: 추출 PDB 파싱 실패 ({exc})")
        else:
            extraction_warnings.append(f"{design_id}: 추출 PDB가 없습니다 ({pdb_path.name})")
        rows.append({
            "design_id": design_id,
            "sequence": sequence,
            "rf2_pae": row.get("rf2_pae", float("nan")),
            "rf2_plddt": row.get("rf2_plddt", float("nan")),
            "rmsd": row.get("rmsd", float("nan")),
            "rf2_pae_scope": row.get("rf2_pae_scope", "unknown"),
            "rf2_plddt_scale": row.get("rf2_plddt_scale", "unknown"),
            "rf2_rmsd_scope": row.get("rf2_rmsd_scope", "unknown"),
            "rf2_native_metrics": row.get("rf2_native_metrics", "{}"),
            "pred_dir": str(pdb_path) if pdb_path.exists() else None,
            "target_chain": target_chain,
            "binder_chain": binder_chain,
            "source": "rfantibody",
            # 복원 실패 후보를 복원 성공으로 기록하지 않는다(후보별 실제 결과만 기록).
            "context_restored": bool(restored),
            "tier2_requested": design_id in selected_tags,
            "design_screening_reason": None if design_id in selected_tags else
                "RF2 사전 필터 또는 최종 복합체 평가 수 제한으로 재예측하지 않았습니다.",
        })
    generated = pd.DataFrame(rows, columns=["design_id", "sequence", "rf2_pae", "rf2_plddt", "rmsd", "pred_dir",
                                           "target_chain", "binder_chain", "source", "context_restored",
                                           "tier2_requested", "design_screening_reason", "rf2_pae_scope",
                                           "rf2_plddt_scale", "rf2_rmsd_scope", "rf2_native_metrics"])
    if not dry_run:
        display = generated.copy()
        display["cif_path"] = display["pred_dir"]
        display["structure_stage"] = "rf2"
        display["exclusion_reason"] = display["design_screening_reason"]
        display.to_csv(phase_dir / "all_generated_candidates.csv", index=False)
        if extraction_warnings:
            (phase_dir / "generation_failure.json").write_text(json.dumps({
                "status": "failed", "stage": "rf2_extraction", "errors": extraction_warnings,
                "generated_candidates": len(generated), "score_file": score_path.name,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            raise shell.ToolError("RFantibody required generated structure extraction failed: " + "; ".join(extraction_warnings))
    candidates = generated[generated["tier2_requested"]].copy() if not generated.empty else generated
    return candidates, {
        "engine": "rfantibody",
        "requested_run_seed": cfg_get(cfg, "run.seed"),
        "seed_control": "no_explicit_seed_argument_in_pinned_stage_clis",
        "stage_randomness_control": {
            "rfdiffusion": {"explicit_seed_argument": False, "deterministic_toggle": bool(cfg_get(cfg, "design.deterministic", False))},
            "proteinmpnn": {"explicit_seed_argument": False},
            "rf2": {"explicit_seed_argument": False},
        },
        "paired_random_state_established": False,
        "env": str(env),
        "framework": str(framework),
        "hotspots": hotspot_arg,
        "num_requested": num_designs,
        "filters": filter_info,
        "score_file": str(score_path),
        "warnings": qvscore_warnings + extraction_warnings,
        "target_context_mapping": context["mapping_path"] if context is not None else None,
        "rf2_score_scope": "pooled_antigen" if context is not None else "single_antigen",
        "quivers": [str(q) for q in backbone_quivers + [combined_quiver, design_quiver, predictions_quiver]],
        "resumed_stages": resumed_stages,
        "all_generated_candidates": len(generated),
        "final_prediction_requested": len(candidates),
    }


def apply_design_filters(scores: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """RF2 1차 필터: pAE 상한 및 설계-RF2 RMSD 상한 (값이 없는 후보는 보존)."""
    enabled = bool(cfg_get(cfg, "design.filters.enabled", True))
    info = {"before": int(len(scores)), "enabled": enabled, "pae_max": None, "rmsd_max": None}
    if scores.empty or not enabled:
        info["after"] = int(len(scores))
        return scores, info
    frame = scores.copy()
    pae_max = float(cfg_get(cfg, "design.filters.rf2_pae", 10.0))
    rmsd_max = float(cfg_get(cfg, "design.filters.design_rmsd", 2.0))
    if "rf2_pae" in frame.columns and frame["rf2_pae"].notna().any():
        frame = frame[frame["rf2_pae"].isna() | (frame["rf2_pae"] <= pae_max)]
        info["pae_max"] = pae_max
    if "rmsd" in frame.columns and frame["rmsd"].notna().any():
        frame = frame[frame["rmsd"].isna() | (frame["rmsd"] <= rmsd_max)]
        info["rmsd_max"] = rmsd_max
    info["after"] = int(len(frame))
    return frame, info


def _parse_rf2_scorefile(path: Path) -> pd.DataFrame:
    """qvscorefile 출력(탭 구분)에서 디자인 ID와 pAE/pLDDT/RMSD 계열 컬럼을 추출."""
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path, sep="\t")
    if frame.shape[1] <= 1:
        frame = pd.read_csv(path, sep=r"\s+")
    if frame.empty:
        return pd.DataFrame()
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    id_column = next((c for c in ("tag", "name", "design_id", "id") if c in frame.columns), None)
    if id_column is None:
        id_column = frame.columns[0]
    result = pd.DataFrame({"design_id": frame[id_column].astype(str)})

    pae_column = next((c for c in ("interaction_pae", "rf2_pae", "pae", "pred_pae") if c in frame), None)
    if pae_column is None:
        pae_column = next((c for c in frame.columns if "pae" in c), None)
    plddt_column = next((c for c in ("predicted_lddt", "rf2_plddt", "plddt") if c in frame), None)
    if plddt_column is None:
        plddt_column = next((c for c in frame.columns if "plddt" in c or "lddt" in c), None)
    rmsd_column = next((c for c in ("target_aligned_antibody_rmsd", "rmsd") if c in frame), None)
    if rmsd_column is None:
        rmsd_column = next((c for c in frame.columns if "rmsd" in c), None)

    result["rf2_pae"] = frame[pae_column].astype(float) if pae_column else float("nan")
    result["rf2_plddt"] = frame[plddt_column].astype(float) if plddt_column else float("nan")
    result["rmsd"] = frame[rmsd_column].astype(float) if rmsd_column else float("nan")
    result["rf2_pae_scope"] = "interaction" if pae_column == "interaction_pae" else "legacy_or_unspecified"
    result["rf2_plddt_scale"] = "0-1" if plddt_column == "predicted_lddt" else "legacy_or_unspecified"
    result["rf2_rmsd_scope"] = "target_aligned_antibody" if rmsd_column == "target_aligned_antibody_rmsd" else "legacy_or_unspecified"
    result["rf2_native_metrics"] = [json.dumps({key: None if pd.isna(value) else value for key, value in row.items()},
                                                default=str, sort_keys=True, allow_nan=False)
                                      for row in frame.to_dict("records")]
    result["sequence"] = frame["sequence"] if "sequence" in frame.columns else ""
    return result


def mock_mutate_sequence(base: str, rng, n_mutations: int, lo: float = 0.72, hi: float = 0.92) -> str:
    """mock 전용: CDR3 인근 구간에 무작위 치환을 도입해 다양한 클러스터를 만든다."""
    sequence = list(base)
    start = int(len(sequence) * lo)
    end = max(start + 1, int(len(sequence) * hi))
    for _ in range(n_mutations):
        position = int(rng.integers(start, end))
        current = sequence[position]
        choices = [aa for aa in "ACDEFGHIKLMNPQRSTVWY" if aa != current]
        sequence[position] = str(rng.choice(choices))
    return "".join(sequence)


def _mock_design(
    cfg: dict,
    phase_dir: Path,
    target_info: dict,
    framework: Path | None,
) -> tuple[pd.DataFrame, dict]:
    num_designs = int(cfg_get(cfg, "design.num_designs", 20))
    seed = int(cfg_get(cfg, "run.seed", 42))
    rng = np.random.default_rng(seed)
    base = framework_sequence(framework)
    target_seq = target_info["sequence"]
    hotspots_int = [int(value) for value in (target_info.get("hotspot_seq_positions") or [])] \
        or [int(label.split(":")[1]) for label in target_info["hotspot_labels"]]
    pred_root = phase_dir / "mock_predictions"
    pred_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for index in range(num_designs):
        design_id = f"design_{index:04d}"
        quality = float(rng.random()) if num_designs > 3 else float(index / max(1, num_designs - 1))
        sequence = mock_mutate_sequence(base, rng, int(rng.integers(2, 7)))
        out_dir = pred_root / design_id
        mock_mod.make_mock_chai_output(out_dir, design_id, target_seq, sequence, hotspots_int, seed=seed, quality=quality)
        rows.append({
            "design_id": design_id,
            "sequence": sequence,
            "rf2_pae": round(2.0 + 12.0 * (1.0 - quality), 2),
            "rf2_plddt": round(0.55 + 0.40 * quality, 3),
            "rmsd": round(4.0 * (1.0 - quality), 2),
            "pred_dir": str(out_dir),
            "source": "mock",
            "mock_quality": quality,
        })
    return pd.DataFrame(rows), {"engine": "mock", "num_requested": num_designs}
