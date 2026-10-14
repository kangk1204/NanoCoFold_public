from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import mock as mock_mod
from . import provenance
from .diagnostics import _engine_extra_env
from .config import cfg_get, cfg_seed, resolve_path

from .target_prep import find_boltz_prediction_dir
from .utils import msa as msa_utils, pdb_io
from .utils import seq as seq_utils, shell
from .utils.tool_args import BOLTZ_MANAGED, CHAI_MANAGED, validate_extra_args

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"
CLIENT_SCRIPT = SCRIPTS_DIR / "chai_fold.py"
from .utils.misc import to_native


def write_chai_fasta(records: dict[str, str], path: Path) -> Path:
    """Chai-1 입력 FASTA (entity name = 체인 이름)."""
    lines: list[str] = []
    for name, sequence in records.items():
        seq_utils.validate_candidate_id(name)
        if "|" in name or not sequence or any(aa not in seq_utils.AA1 for aa in sequence):
            raise ValueError("Chai FASTA는 안전한 체인 이름과 표준 아미노산 대문자 서열이 필요합니다.")
        lines.append(f">protein|name={name}")
        lines.append(sequence)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_restraints_csv(
    hotspot_positions: list[int],
    target_sequence: str,
    target_name: str,
    binder_name: str,
    path: Path,
    connection_type: str = "pocket",
    max_distance: float = 11.0,
) -> Path:
    """Chai-1 restraint CSV. hotspot_positions 는 타깃 서열의 1-based 인덱스다.

    (주의: PDB 잔기번호가 아니다. target_prep 이 PDB번호→서열인덱스로 변환해 전달한다.)

    pocket 만 지원한다: Chai-1 의 contact restraint 는 양쪽 모두 잔기 토큰을 요구하지만
    (chai_lab restraints.py: "A should specify a token/atom"), 스크리닝 단계에서는
    바인더 쪽 접촉 잔기를 알 수 없으므로 표현할 수 없다(R9-1).
    """
    if str(connection_type).lower() != "pocket":
        raise ValueError(
            f"지원하지 않는 screen.restraint.connection_type: {connection_type}. "
            "Chai-1 contact restraint 는 바인더 쪽 잔기 번호가 필요해 스크리닝에서 사용할 수 없습니다 "
            "(pocket 만 사용하세요)."
        )
    rows = [
        "restraint_id,chainA,res_idxA,chainB,res_idxB,connection_type,confidence,"
        "min_distance_angstrom,max_distance_angstrom,comment"
    ]
    for index, position in enumerate(hotspot_positions):
        if isinstance(position, (bool, np.bool_)) or not isinstance(position, (int, np.integer)) or not 1 <= position <= len(target_sequence):
            raise ValueError("Hotspot restraint 위치는 표적 서열 범위 안의 1-based 정수여야 합니다.")
        aa = target_sequence[position - 1]
        rows.append(
            f"restraint{index},{binder_name},,{target_name},{aa}{position},pocket,"
            f"1.0,0.0,{max_distance},hotspot_seq{position}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def complex_chain_records(target_info: dict, binder_sequence: str) -> tuple[dict[str, str], list[tuple[str, str]], str]:
    """타깃(A) + 보조 체인(B, C, ...) + 나노바디(마지막 체인) 순서의 FASTA 레코드.

    복합체의 단백질 파트너을 예측에 포함해 코일드코일 등 접힘 의존 에피토프를 다룬다.
    반환: (chain_records, aux_pairs, binder_chain_letter)
    """
    records: dict[str, str] = {"A": seq_utils.clean_sequence(str(target_info["sequence"]))}
    aux_pairs: list[tuple[str, str]] = []
    aux_chains = target_info.get("aux_chains") or []
    for entry in aux_chains:
        sequence = seq_utils.clean_sequence(str(entry.get("sequence", "")))
        if not sequence:
            continue
        letter = chr(ord("A") + len(records))
        records[letter] = sequence
        aux_pairs.append((str(entry.get("name", letter)), letter))
    binder_letter = chr(ord("A") + len(records))
    records[binder_letter] = seq_utils.clean_sequence(binder_sequence)
    return records, aux_pairs, binder_letter


def write_boltz_yaml(
    chains: list[dict],
    path: Path,
    hotspot_positions: list[int] | None = None,
    target_id: str = "A",
    binder_id: str = "B",
    max_distance: float = 6.0,
) -> Path:
    """chains: [{"id","sequence","msa"}...] 순서대로 YAML 기록.

    hotspot_positions 는 타깃 서열의 1-based 인덱스다 (PDB 번호 아님).
    """
    sequences = [
        {"protein": {"id": chain["id"], "sequence": chain["sequence"], "msa": chain.get("msa", "empty")}}
        for chain in chains
    ]
    payload: dict = {"version": 1, "sequences": sequences}
    if hotspot_positions:
        contacts = [[target_id, int(position)] for position in hotspot_positions]
        payload["constraints"] = [
            {"pocket": {"binder": binder_id, "contacts": contacts, "max_distance": max_distance}}
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(to_native(payload), sort_keys=False), encoding="utf-8")
    return path


def predict_complexes(
    cfg: dict,
    work_dir: Path,
    target_info: dict,
    candidates: pd.DataFrame,
    engine: str | None = None,
    mock: bool = False,
    dry_run: bool = False,
    id_column: str = "design_id",
    sequence_column: str = "sequence",
) -> pd.DataFrame:
    """후보 서열들을 타깃(+보조 체인)과의 복합체로 예측한다."""
    from .input_review import validate_reviewed_input
    validate_reviewed_input(cfg)
    seq_utils.validate_candidate_ids(candidates[id_column].astype(str))
    work_dir.mkdir(parents=True, exist_ok=True)
    engine = engine or str(cfg_get(cfg, "screen.engine", "chai1"))
    if engine == "colabfold":
        from .colabfold import predict
        return predict(cfg, work_dir, target_info, candidates, mock=mock, dry_run=dry_run,
                       id_column=id_column, sequence_column=sequence_column)
    if not dry_run:
        # A small stage-local plan lets progress readers use the actual selected
        # pool in every mode, including tier-2 and mature, without reranking it.
        plan = {"attempt_id": uuid.uuid4().hex, "engine": engine,
                "candidate_ids": [seq_utils.validate_candidate_id(str(v)) for v in candidates[id_column]]}
        (work_dir / "prediction_plan.json").write_text(json.dumps(plan), encoding="utf-8")
        progress = {"attempt_id": plan["attempt_id"], "completed": []}
        (work_dir / "prediction_progress.json").write_text(json.dumps(progress), encoding="utf-8")
    target_sequence = target_info["sequence"]
    hotspot_positions = target_info.get("hotspot_seq_positions")
    if hotspot_positions is None and target_info.get("hotspot_labels"):
        raise ValueError(
            "핫스팟 서열 위치 정보가 없습니다. target_prep 이 생성한 hotspot_seq_positions 를 사용해야 합니다 "
            "(PDB 번호와 서열 인덱스 혼용 방지)."
        )
    hotspot_positions = [int(value) for value in (hotspot_positions or [])]
    if not bool(cfg_get(cfg, "screen.restraint.enabled", True)):
        hotspot_positions = []
    target_msa = target_info.get("target_msa") or "empty"
    seed = cfg_seed(cfg, "run.seed")
    records: list[dict] = []
    run_root = work_dir.parent.resolve()

    for _, row in candidates.iterrows():
        if records and not dry_run:
            # Preserve validated preceding queries even when the next tool fails.
            _predictions_manifest_frame(pd.DataFrame(records), run_root).to_csv(work_dir / "predictions.csv", index=False)
        candidate_id = seq_utils.validate_candidate_id(str(row[id_column]))
        sequence = str(row[sequence_column])
        candidate_dir = work_dir / candidate_id
        candidate_dir.mkdir(parents=True, exist_ok=True)

        if mock:
            if not dry_run:
                mock_mod.make_mock_chai_output(
                    candidate_dir, candidate_id, target_sequence, sequence, hotspot_positions, seed=seed
                )
            records.append({
                id_column: candidate_id,
                "pred_dir": str(candidate_dir.resolve()),
                "engine": "mock",
                "target_chain": "A",
                "binder_chain": "B",
            })
            continue

        chain_records, aux_pairs, binder_letter = complex_chain_records(target_info, sequence)
        binder_msa = target_info.get("binder_msa") or "empty"
        if bool(cfg_get(cfg, "screen.binder_msa", False)) and binder_msa == "empty" and not dry_run:
            binder_msa, msa_warnings = msa_utils.ensure_a3m(
                sequence, cfg_get(cfg, "structure.msa", {}) or {}, run_dir=candidate_dir
            )
            for warning in msa_warnings:
                print(f"  [msa:{candidate_id}] {warning}")
        if engine == "chai1":
            msa_used = _predict_chai1(cfg, candidate_dir, target_info, chain_records, binder_letter, hotspot_positions, dry_run)
            records.append({
                id_column: candidate_id,
                "pred_dir": str((candidate_dir / "chai_out").resolve()),
                "engine": "chai1",
                "msa_used": msa_used or "chai1_single",
                "target_chain": "A",
                "binder_chain": binder_letter,
                "aux_chains": ";".join(f"{name}:{letter}" for name, letter in aux_pairs),
            })
        elif engine == "opendde":
            from .opendde import predict as opendde_predict
            if hotspot_positions:
                raise ValueError("OpenDDE는 hotspot 예측 제약을 지원하지 않습니다. restraint를 끄고 사후 접촉 지표로 평가하세요.")
            alignments = target_info.get("chain_msas")
            if not alignments and (target_msa != "empty" or binder_msa != "empty"):
                alignments = {"A": target_msa, binder_letter: binder_msa}
                if any(value == "empty" for value in alignments.values()) or aux_pairs:
                    raise ValueError("OpenDDE MSA는 모든 체인에 필요합니다. 일부 MSA를 무시하거나 자동 검색하지 않습니다.")
            directory = opendde_predict(cfg, candidate_dir, chain_records,
                                        chain_msas=alignments, dry_run=dry_run)
            records.append({id_column: candidate_id, "pred_dir": str(directory.resolve()), "engine": engine,
                            "msa_used": "precomputed_unpaired" if alignments else "empty",
                            "target_chain": "A", "binder_chain": binder_letter,
                            "aux_chains": ";".join(f"{name}:{letter}" for name, letter in aux_pairs)})
        elif engine == "boltz2":
            _predict_boltz2(cfg, candidate_dir, chain_records, target_msa, binder_letter, hotspot_positions, dry_run, binder_msa,
                            chain_msas=target_info.get("chain_msas"))
            nested = find_boltz_prediction_dir(candidate_dir / "boltz_out") or candidate_dir
            records.append({
                id_column: candidate_id,
                "pred_dir": str((nested if Path(nested).exists() else candidate_dir).resolve()),
                "engine": "boltz2",
                "target_chain": "A",
                "binder_chain": binder_letter,
                "aux_chains": ";".join(f"{name}:{letter}" for name, letter in aux_pairs),
            })
        else:
            raise ValueError(f"지원하지 않는 예측 엔진: {engine}")

        if not dry_run and not mock:
            marker = candidate_dir / ("boltz_complete.json" if engine == "boltz2" else "prediction_complete.json")
            if _read_json_object(marker).get("status") == "complete":
                # Publish cached completions only after this attempt has checked
                # their input identity and output hashes in the engine wrapper.
                progress["completed"].append(candidate_id)
                temporary = work_dir / "prediction_progress.tmp"
                temporary.write_text(json.dumps(progress), encoding="utf-8")
                temporary.replace(work_dir / "prediction_progress.json")

    predictions = pd.DataFrame(records)
    if not predictions.empty and not dry_run:
        _predictions_manifest_frame(predictions, run_root).to_csv(work_dir / "predictions.csv", index=False)
    return predictions


def _predictions_manifest_frame(predictions: pd.DataFrame, run_root: Path) -> pd.DataFrame:
    manifest = predictions.copy()
    if "pred_dir" in manifest.columns:
        manifest["pred_dir"] = manifest["pred_dir"].map(lambda value: _manifest_pred_dir(value, run_root))
    return manifest


def _manifest_pred_dir(value, run_root: Path) -> str:
    path = Path(str(value))
    try:
        resolved = path.resolve()
        return str(resolved.relative_to(run_root.resolve()))
    except ValueError:
        return str(path)


def _read_json_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _structure_chain_sequences(path: Path) -> dict[str, str]:
    structure = pdb_io.load_structure(path)
    models = list(structure.get_models())
    if len(models) != 1:
        raise ValueError("예측 파일은 모델 1개를 포함해야 합니다.")
    return {chain.id: sequence for chain in models[0] if (sequence := pdb_io.chain_sequence(chain))}


def _valid_pae_file(path: Path, residue_count: int) -> bool:
    with np.load(path) as data:
        pae_key = "pae" if "pae" in data else "PAE" if "PAE" in data else None
        if pae_key is None:
            return False
        pae = np.asarray(data[pae_key])
        return (
            pae.shape == (residue_count, residue_count)
            and bool(np.isfinite(pae).all())
            and bool((pae >= 0).all())
        )


def _valid_chai_artifacts(
    out_dir: Path, expected_residue_count: int, chain_records: dict[str, str] | None = None,
    expected_models: int = 1,
) -> bool:
    try:
        expected_models = int(expected_models)
        if expected_models < 1:
            return False
        expected_paths = {out_dir / f"pred.model_idx_{index}.cif" for index in range(expected_models)}
        if set(out_dir.glob("pred.model_idx_*.cif")) != expected_paths:
            return False
        expected_chains = (
            {letter: seq_utils.clean_sequence(sequence) for letter, sequence in chain_records.items()}
            if chain_records is not None else None
        )
        for index, cif in enumerate(sorted(expected_paths, key=lambda path: int(path.stem.rsplit("_", 1)[1]))):
            actual_chains = _structure_chain_sequences(cif)
            if (sum(len(sequence) for sequence in actual_chains.values()) != int(expected_residue_count)
                    or expected_chains is not None and actual_chains != expected_chains):
                return False
            scores = out_dir / f"scores_extra_idx_{index}.npz"
            if index == 0 and not scores.is_file():
                scores = out_dir / "scores_extra.npz"  # Existing single-model wrapper compatibility.
            if not _valid_pae_file(scores, expected_residue_count):
                return False
            # Native confidence belongs to this sample; it cannot be borrowed
            # from sample 0 when another requested model is missing.
            with np.load(out_dir / f"scores.model_idx_{index}.npz") as data:
                if not data.files:
                    return False
        return True
    except Exception:  # noqa: BLE001 - cache integrity check must treat all parse failures as non-reusable.
        return False


def _valid_boltz_artifacts(
    out_dir: Path, chain_records: dict[str, str] | None = None, expected_models: int = 1,
) -> bool:
    prediction_dir = find_boltz_prediction_dir(out_dir) or out_dir
    try:
        structures = sorted(prediction_dir.rglob("*.cif")) + sorted(prediction_dir.rglob("*.pdb"))
        if int(expected_models) < 1 or len(structures) != int(expected_models):
            return False
        stems = [path.stem.rsplit("_model_", 1) for path in structures]
        if (any(len(parts) != 2 for parts in stems) or len({parts[0] for parts in stems}) != 1
                or {parts[1] for parts in stems} != {str(index) for index in range(int(expected_models))}):
            return False
        expected_chains = (
            {letter: seq_utils.clean_sequence(sequence) for letter, sequence in chain_records.items()}
            if chain_records is not None else None
        )
        for structure in structures:
            actual_chains = _structure_chain_sequences(structure)
            residue_count = sum(len(sequence) for sequence in actual_chains.values())
            if residue_count < 1 or expected_chains is not None and actual_chains != expected_chains:
                return False
            confidence_path = structure.parent / f"confidence_{structure.stem}.json"
            pae_path = structure.parent / f"pae_{structure.stem}.npz"
            confidence = json.loads(confidence_path.read_text(encoding="utf-8"))
            if not isinstance(confidence, dict) or not _valid_pae_file(pae_path, residue_count):
                return False
        return True
    except Exception:  # noqa: BLE001 - malformed cached artifacts are not reusable.
        return False


def _clear_prediction_markers(*paths: Path) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


class _InvalidPredictionArtifacts(shell.ToolError):
    """A zero exit code cannot establish valid model/input identity."""


def _predict_chai1(
    cfg: dict,
    candidate_dir: Path,
    target_info: dict,
    chain_records: dict[str, str],
    binder_letter: str,
    hotspot_positions: list[int],
    dry_run: bool,
) -> str | None:
    extra = validate_extra_args(cfg_get(cfg, "screen.extra_args", []), setting="screen.extra_args",
                                managed=CHAI_MANAGED, allow_abbrev=True)
    env = resolve_path(cfg, cfg_get(cfg, "screen.env", cfg_get(cfg, "structure.chai1.env")))
    suffix = ".dry-run" if dry_run else ""
    fasta = write_chai_fasta(chain_records, candidate_dir / f"complex{suffix}.fasta")
    restraint_path = None
    if bool(cfg_get(cfg, "screen.restraint.enabled", True)):
        restraint_path = write_restraints_csv(
            hotspot_positions,
            # FASTA에 기록한 것과 동일한 정규화 서열로 인덱싱한다(위치 밀림 방지).
            chain_records.get("A", target_info["sequence"]),
            "A",
            binder_letter,
            candidate_dir / f"restraints{suffix}.csv",
            connection_type=str(cfg_get(cfg, "screen.restraint.connection_type", "pocket")),
            max_distance=float(cfg_get(cfg, "screen.restraint.max_distance", 11.0)),
        )
    samples = int(cfg_get(cfg, "screen.num_diffn_samples", 1))
    if samples > 1:
        print(
            f"  [chai:{candidate_dir.name}] num_diffn_samples={samples}: 후보 대표 점수는 sample 0 기준이며 "
            "전체 sample 구조·신뢰도·PAE는 각각 보존됩니다."
        )
    executable = CLIENT_SCRIPT
    out_dir = candidate_dir / ("chai_out_dry_run" if dry_run else "chai_out")
    msa_mode = str(cfg_get(cfg, "screen.msa_mode", "single")).lower()
    if msa_mode == "msa" and not bool(cfg_get(cfg, "screen.allow_public_msa_server", False)):
        raise ValueError(
            "screen.msa_mode=msa 는 Chai-1 --use-msa-server(ColabFold 공용 서버)를 호출합니다. "
            "LICENSE_MANIFEST의 상업적 사용 제외 항목입니다. 명시 허용: screen.allow_public_msa_server: true "
            "(권장: screen.msa_mode=local 로컬 MMseqs MSA 또는 Boltz-2)."
        )
    msa_directory = None
    if msa_mode == "local" and not dry_run:
        msa_directory = _prepare_chai_msa_directory(cfg, candidate_dir, target_info, target_info.get("target_msa"))
    if msa_mode == "precomputed":
        msa_directory = resolve_path(cfg, cfg_get(cfg, "screen.chai1.msa_directory"))
        if msa_directory is None:
            raise ValueError("Chai precomputed MSA requires screen.chai1.msa_directory")
        if not dry_run:
            _validate_chai_precomputed_directory(msa_directory, chain_records)
    import hashlib

    target_msa_path = target_info.get("target_msa")
    msa_content_hash = None
    if msa_mode == "local" and target_msa_path and target_msa_path != "empty" and Path(str(target_msa_path)).exists():
        msa_content_hash = hashlib.sha256(Path(str(target_msa_path)).read_bytes()).hexdigest()[:16]
    input_fingerprint = hashlib.sha256(
        json.dumps({
            "sequence": chain_records,
            "hotspot_positions": list(hotspot_positions),
            "seed": cfg_get(cfg, "run.seed"),
            "msa_mode": cfg_get(cfg, "screen.msa_mode", "single"),
            "target_msa": target_msa_path,
            "target_msa_content": msa_content_hash,
            "precomputed_msa": provenance.output_manifest(msa_directory) if msa_mode == "precomputed" and not dry_run else None,
            "recycles": cfg_get(cfg, "screen.num_trunk_recycles"),
            "timesteps": cfg_get(cfg, "screen.num_diffn_timesteps"),
            "samples": cfg_get(cfg, "screen.num_diffn_samples"),
            "restraint": cfg_get(cfg, "screen.restraint"),
            "extra_args": cfg_get(cfg, "screen.extra_args"),
            "runtime": provenance.runtime_identity(cfg, "chai1") if not dry_run else None,
        }, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    fingerprint_path = candidate_dir / "complex_input.json"
    completion_path = candidate_dir / "prediction_complete.json"
    expected_residue_count = sum(len(seq_utils.clean_sequence(sequence)) for sequence in chain_records.values())
    stored = _read_json_object(fingerprint_path) if fingerprint_path.exists() else {}
    complete = _read_json_object(completion_path) if completion_path.exists() else {}
    reuse = (
        not dry_run
        and cfg_get(cfg, "run.resume", True)
        and completion_path.exists()
        and complete.get("status") == "complete"
        and stored.get("fingerprint") == input_fingerprint
        and provenance.outputs_match(out_dir, complete.get("outputs"))
        and _valid_chai_artifacts(out_dir, expected_residue_count, chain_records, samples)
    )
    if reuse:
        return stored.get("msa_used") or ("chai1_public_msa" if msa_mode == "msa" else
                                          "chai1_local_msa" if stored.get("msa_mode") in {"local", "precomputed"} else
                                          "chai1_single_fallback" if stored.get("msa_mode") == "local_fallback" else "chai1_single")
    if not dry_run and out_dir.exists():
        provenance.archive_existing([out_dir, completion_path, fingerprint_path, candidate_dir / "chai1.log"], candidate_dir)
    if not dry_run:
        _clear_prediction_markers(completion_path, fingerprint_path)  # 이전 성공 marker 무효화(J05)
    argv = [
        str(shell.env_python(env)) if not dry_run else "python", str(executable),
        "--fasta", str(fasta),
        "--out-dir", str(out_dir),
        "--num-trunk-recycles", str(int(cfg_get(cfg, "screen.num_trunk_recycles", 1))),
        "--num-diffn-timesteps", str(int(cfg_get(cfg, "screen.num_diffn_timesteps", 50))),
        "--num-diffn-samples", str(int(cfg_get(cfg, "screen.num_diffn_samples", 1))),
        "--seed", str(cfg_seed(cfg, "run.seed")),
    ]
    if restraint_path is not None:
        argv.extend(["--constraint-path", str(restraint_path)])
    if msa_mode == "msa":
        if not bool(cfg_get(cfg, "screen.allow_public_msa_server", False)):
            raise ValueError(
                "screen.msa_mode=msa 는 Chai-1 --use-msa-server(ColabFold 공용 서버)를 호출합니다. "
                "LICENSE_MANIFEST의 상업적 사용 제외 항목입니다. 명시 허용: screen.allow_public_msa_server: true "
                "(권장: screen.msa_mode=local 로컬 MMseqs MSA 또는 Boltz-2)."
            )
        argv.append("--use-msa-server")
    if msa_directory is not None:
        argv.extend(["--msa-directory", str(msa_directory)])
    argv.extend(extra)
    env_vars = _engine_extra_env(cfg, "chai1")
    try:
        log_path = candidate_dir / ("chai1.dry-run.log" if dry_run else "chai1.log")
        shell.run_in_env(env, argv, extra_env=env_vars, log_path=log_path, dry_run=dry_run)
        used = "chai1_public_msa" if msa_mode == "msa" else "chai1_local_msa" if msa_directory is not None else "chai1_single"
        if not dry_run:
            if _valid_chai_artifacts(out_dir, expected_residue_count, chain_records, samples):
                fingerprint_path.write_text(
                    json.dumps({"fingerprint": input_fingerprint, "msa_mode": msa_mode, "msa_used": used}),
                    encoding="utf-8",
                )
                completion_path.write_text(json.dumps({"status": "complete", "msa_used": used,
                                                       "outputs": provenance.output_manifest(out_dir)}), encoding="utf-8")
            else:
                _clear_prediction_markers(completion_path, fingerprint_path)
                raise _InvalidPredictionArtifacts(
                    prediction_failure_reason(candidate_dir)
                    or "Chai inference did not produce valid structure/full PAE outputs for every requested model and exact input chain sequence"
                )
        return used
    except (shell.ExecutionCancelled, TimeoutError, subprocess.TimeoutExpired):
        _clear_prediction_markers(completion_path, fingerprint_path)
        raise
    except shell.ToolError as exc:
        _clear_prediction_markers(completion_path, fingerprint_path)
        if (msa_directory is None or msa_mode == "precomputed"
                or isinstance(exc, (_InvalidPredictionArtifacts, shell.ExecutionCancelled, TimeoutError))):
            raise
        print(f"  [chai:{candidate_dir.name}] local MSA 실패 → 단일서열로 재시도: {exc}")
        fallback = [a for a in argv if a not in ("--msa-directory", str(msa_directory))]
        # Keep the failed native attempt intact before the fallback writes into
        # the same destination. Its structures and log explain why MSA was not
        # used, and must not be merged into the successful single-sequence run.
        provenance.archive_existing([out_dir, candidate_dir / "chai1.log"], candidate_dir)
        shell.run_in_env(env, fallback, extra_env=env_vars, log_path=candidate_dir / "chai1_retry.log", dry_run=dry_run)
        used = "chai1_single_fallback"
        if _valid_chai_artifacts(out_dir, expected_residue_count, chain_records, samples):
            fingerprint_path.write_text(
                json.dumps({"fingerprint": input_fingerprint, "msa_mode": "local_fallback", "msa_used": used}),
                encoding="utf-8",
            )
            completion_path.write_text(json.dumps({"status": "complete", "msa_used": used,
                                                   "outputs": provenance.output_manifest(out_dir)}), encoding="utf-8")
        else:
            _clear_prediction_markers(completion_path, fingerprint_path)
            raise _InvalidPredictionArtifacts(
                prediction_failure_reason(candidate_dir)
                or "Chai retry did not produce every requested model with exact input chain sequences and full PAE"
            )
        return used


def _validate_chai_precomputed_directory(directory: Path, chains: dict[str, str]) -> dict:
    import hashlib
    receipt = _read_json_object(directory / "conversion_receipt.json")
    if receipt.get("schema_version") != 1 or receipt.get("pairing") != "unpaired":
        raise ValueError("Chai precomputed MSA conversion receipt missing/corrupt")
    if set(receipt.get("chains", {})) != set(chains):
        raise ValueError("Chai precomputed MSA must contain every actual chain")
    for chain, sequence in chains.items():
        entry = receipt["chains"][chain]
        expected_name = hashlib.sha256(sequence.encode()).hexdigest() + ".aligned.pqt"
        path = directory / expected_name
        if (entry.get("file") != expected_name or entry.get("sequence_sha256") != hashlib.sha256(sequence.encode()).hexdigest()
                or not path.is_file() or entry.get("sha256") != provenance.file_digest(path)
                or type(entry.get("parsed_rows")) is not int or entry["parsed_rows"] < 2):
            raise ValueError(f"Chai precomputed MSA content/query/depth mismatch for chain {chain}")
    return receipt


def prepare_chai_precomputed_msa(cfg: dict, directory: Path, chains: dict[str, str], monomers: dict) -> Path:
    """Convert all exact frozen monomers once, with hash-verified cache reuse."""
    import hashlib
    from .colabfold_msa import read_monomer
    directory = Path(directory)
    inputs = {}
    texts = {}
    if set(chains) != set(monomers):
        raise ValueError("Chai conversion needs exact MSA for every chain")
    for chain, sequence in chains.items():
        entry = monomers[chain]
        alignment = read_monomer(entry["a3m"], sequence, cfg_get(cfg, "screen.colabfold.msa_max_sequences", 2048))
        if entry.get("sha256") and entry["sha256"] != alignment.source_sha256:
            raise ValueError("Chai source A3M differs from trusted monomer receipt")
        text = alignment.text()
        filename = "chain_" + chain + ".a3m"
        texts[filename] = text
        inputs[chain] = {"sequence": sequence, "a3m": filename, "sha256": hashlib.sha256(text.encode()).hexdigest(),
                         "source_a3m_sha256": alignment.source_sha256}
    payload = {"schema_version": 1, "chains": inputs, "converter_sha256": provenance.file_digest(SCRIPTS_DIR / "chai_msa_from_a3m.py")}
    fingerprint = provenance.fingerprint(payload)
    payload["inputs_fingerprint"] = fingerprint
    existing = _read_json_object(directory / "manifest.json")
    if existing:
        if existing != payload:
            raise ValueError("Chai MSA conversion inputs changed; preserve output and use a new run")
        try:
            receipt = _validate_chai_precomputed_directory(directory, chains)
            if receipt.get("inputs_fingerprint") == fingerprint:
                return directory
        except ValueError:
            provenance.archive_existing(list(directory.glob("*.aligned.pqt")) + [directory / "conversion_receipt.json"], directory)
    directory.mkdir(parents=True, exist_ok=True)
    for filename, text in texts.items():
        (directory / filename).write_text(text)
    manifest = directory / "manifest.json"
    manifest.write_text(json.dumps(payload, indent=2) + "\n")
    env = resolve_path(cfg, cfg_get(cfg, "screen.env", cfg_get(cfg, "structure.chai1.env")))
    shell.run_in_env(env, [str(shell.env_python(env)), str(SCRIPTS_DIR / "chai_msa_from_a3m.py"), "--manifest", str(manifest)],
                     extra_env=_engine_extra_env(cfg, "chai1"), log_path=directory / "conversion.log")
    _validate_chai_precomputed_directory(directory, chains)
    return directory


def _prepare_chai_msa_directory(cfg: dict, candidate_dir: Path, target_info: dict, msa_path: str | None = None) -> Path | None:
    """Publish a receipt-verified target MSA cache only after full conversion."""
    if not msa_path or msa_path == "empty" or not Path(str(msa_path)).exists():
        print("  [chai] local MSA: 캐시된 타깃 a3m이 없어 단일서열로 진행합니다.")
        return None
    import fcntl
    from uuid import uuid4
    from .utils.atomic import check_output
    source = Path(str(msa_path))
    chains = {"A": seq_utils.clean_sequence(target_info["sequence"])}
    cache = candidate_dir.parent / "chai_msa_input"
    check_output(cache, candidate_dir.parent)
    cache.mkdir(parents=True, exist_ok=True)
    identity = {"source_a3m_sha256": provenance.file_digest(source), "chains": chains,
                "converter_sha256": provenance.file_digest(SCRIPTS_DIR / "chai_msa_from_a3m.py"),
                "max_sequences": cfg_get(cfg, "screen.colabfold.msa_max_sequences", 2048)}
    destination = cache / provenance.fingerprint(identity)
    check_output(destination, cache)
    with (cache / ".conversion.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if destination.exists():
            try:
                receipt = _validate_chai_precomputed_directory(destination, chains)
                manifest = _read_json_object(destination / "manifest.json")
                if (receipt.get("inputs_fingerprint") == manifest.get("inputs_fingerprint")
                        and manifest.get("converter_sha256") == identity["converter_sha256"]
                        and manifest.get("chains", {}).get("A", {}).get("source_a3m_sha256") == identity["source_a3m_sha256"]):
                    return destination
            except ValueError:
                pass  # Preserve corrupt published cache until a verified repair exists.
        attempts = cache / ".attempts"
        check_output(attempts, cache)
        attempts.mkdir(exist_ok=True)
        staged = attempts / uuid4().hex
        # Conversion writes only to a fresh, unpublished directory. Failed
        # attempts and prior cache bytes remain available for diagnosis/retry.
        prepare_chai_precomputed_msa(cfg, staged, chains,
                                    {"A": {"a3m": str(source), "sha256": identity["source_a3m_sha256"]}})
        _validate_chai_precomputed_directory(staged, chains)
        if provenance.file_digest(source) != identity["source_a3m_sha256"]:
            raise ValueError("Chai 로컬 MSA 원본이 변환 중 변경되었습니다. 기존 자료를 보존하고 다시 준비하세요.")
        if destination.exists():
            provenance.archive_existing([destination], cache)
        check_output(destination, cache)
        staged.replace(destination)
        return destination


def _predict_boltz2(
    cfg: dict,
    candidate_dir: Path,
    chain_records: dict[str, str],
    target_msa: str,
    binder_letter: str,
    hotspot_positions: list[int],
    dry_run: bool,
    binder_msa: str = "empty",
    chain_msas: dict[str, str] | None = None,
) -> None:
    import hashlib
    import json as _json

    extra = validate_extra_args(cfg_get(cfg, "score.boltz2_extra_args", []),
                                setting="score.boltz2_extra_args", managed=BOLTZ_MANAGED)
    out_dir = candidate_dir / "boltz_out"
    cpu_fp32 = bool(cfg_get(cfg, "score.boltz2_cpu_fp32", False))
    if cpu_fp32:
        from .scripts.boltz_cpu_fp32 import validate_cpu_args
        validate_cpu_args(["predict", *extra])

    def _msa_hash(path_value):
        if not path_value or str(path_value) in ("empty", "none"):
            return str(path_value)
        p = Path(str(path_value))
        if not p.exists():
            return f"missing:{path_value}"
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]

    input_fingerprint = hashlib.sha256(
        _json.dumps({
            "chains": chain_records,
            "binder_letter": binder_letter,
            "hotspot_positions": list(hotspot_positions),
            "target_msa": target_msa,
            "target_msa_content": _msa_hash(target_msa),
            "binder_msa": binder_msa,
            "binder_msa_content": _msa_hash(binder_msa),
            "chain_msas": {letter: {"path": value, "sha256": _msa_hash(value)} for letter, value in (chain_msas or {}).items()},
            "num_subsampled_msa": cfg_get(cfg, "score.boltz2_num_subsampled_msa"),
            "seed": cfg_seed(cfg, "score.boltz2.seed"),
            "recycles": cfg_get(cfg, "score.boltz2_recycling_steps"),
            "diffusion_samples": cfg_get(cfg, "score.boltz2_diffusion_samples", 1),
            "pocket_max_distance": cfg_get(cfg, "screen.boltz2_pocket_max_distance"),
            "extra_args": cfg_get(cfg, "score.boltz2_extra_args"),
            "runtime": provenance.runtime_identity(cfg, "boltz2") if not dry_run else None,
            **({"cpu_precision": 32} if cpu_fp32 else {}),
        }, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    fingerprint_path = candidate_dir / "boltz_input.json"
    completion_path = candidate_dir / "boltz_complete.json"
    stored = _read_json_object(fingerprint_path) if fingerprint_path.exists() else {}
    complete = _read_json_object(completion_path) if completion_path.exists() else {}
    reuse = (
        not dry_run
        and cfg_get(cfg, "run.resume", True)
        and completion_path.exists()
        and complete.get("status") == "complete"
        and stored.get("fingerprint") == input_fingerprint
        and provenance.outputs_match(out_dir, complete.get("outputs"))
        and _valid_boltz_artifacts(out_dir, chain_records, int(cfg_get(cfg, "score.boltz2_diffusion_samples", 1)))
    )
    if reuse:
        return
    if not dry_run and out_dir.exists():
        provenance.archive_existing([out_dir, completion_path, fingerprint_path, candidate_dir / "boltz2.log"], candidate_dir)
    if not dry_run:
        _clear_prediction_markers(completion_path, fingerprint_path)
    env = resolve_path(cfg, cfg_get(cfg, "screen.boltz2_env", cfg_get(cfg, "structure.boltz2.env")))
    chains = [
        {
            "id": letter,
            "sequence": sequence,
            "msa": (chain_msas or {}).get(letter, target_msa if letter == "A" else (binder_msa if letter == binder_letter else "empty")),
        }
        for letter, sequence in chain_records.items()
    ]
    suffix = ".dry-run" if dry_run else ""
    yaml_path = write_boltz_yaml(
        chains,
        candidate_dir / f"complex{suffix}.yaml",
        hotspot_positions=hotspot_positions,
        target_id="A",
        binder_id=binder_letter,
        max_distance=float(cfg_get(cfg, "screen.boltz2_pocket_max_distance", 6.0)),
    )
    executable = shell.env_executable(env, "python" if cpu_fp32 else "boltz", dry_run=dry_run)
    command = [str(executable), str(SCRIPTS_DIR / "boltz_cpu_fp32.py")] if cpu_fp32 else [str(executable)]
    argv = [
        *command, "predict", str(yaml_path),
        "--out_dir", str(candidate_dir / ("boltz_out_dry_run" if dry_run else "boltz_out")),
        "--output_format", "mmcif",
        "--recycling_steps", str(int(cfg_get(cfg, "score.boltz2_recycling_steps", 3))),
        "--diffusion_samples", str(int(cfg_get(cfg, "score.boltz2_diffusion_samples", 1))),
        "--seed", str(cfg_seed(cfg, "score.boltz2.seed")),
        "--write_full_pae",
        "--subsample_msa",
        "--num_subsampled_msa", str(int(cfg_get(cfg, "score.boltz2_num_subsampled_msa", 512))),
    ]
    argv.extend(extra)
    if not dry_run:
        argv.append("--override")
    env_vars = _engine_extra_env(cfg, "boltz2")
    try:
        log_path = candidate_dir / ("boltz2.dry-run.log" if dry_run else "boltz2.log")
        shell.run_in_env(env, argv, extra_env=env_vars, log_path=log_path, dry_run=dry_run)
    except Exception:
        if not dry_run:
            _clear_prediction_markers(completion_path, fingerprint_path)
        raise
    if not dry_run:
        if _valid_boltz_artifacts(out_dir, chain_records, int(cfg_get(cfg, "score.boltz2_diffusion_samples", 1))):
            fingerprint_path.write_text(_json.dumps({"fingerprint": input_fingerprint}), encoding="utf-8")
            completion_path.write_text(_json.dumps({"status": "complete",
                                                   "outputs": provenance.output_manifest(out_dir)}), encoding="utf-8")
        else:
            _clear_prediction_markers(completion_path, fingerprint_path)  # 필수 산출물 없음 → 완료 아님(J05)
            raise _InvalidPredictionArtifacts(
                prediction_failure_reason(candidate_dir)
                or "Boltz inference did not produce every requested model with exact input chain sequences and full PAE"
            )


def prediction_failure_reason(path: Path) -> str | None:
    """Preserve the engine's OOM cause even when its CLI returns zero without outputs."""
    path = Path(path)
    if path.is_file():
        path = path.parent
    for name in ("boltz2.log", "chai1.log", "chai1_retry.log"):
        log = path / name
        if not log.is_file():
            continue
        with log.open("rb") as handle:
            handle.seek(max(0, log.stat().st_size - 262144))
            tail = handle.read().decode("utf-8", errors="replace").lower()
        if any(token in tail for token in ("ran out of memory", "out of memory", "allocation failed with oom")):
            return "GPU 메모리 부족 (OOM): 예측 엔진이 산출물을 생성하지 못했습니다. 다른 GPU 작업 종료 후 검증된 단계 재개를 사용하세요."
    return None


def link_prediction_dirs(source_dirs: list[str], destination_root: Path) -> None:
    """티어별 예측 디렉터리를 하나의 루트로 심볼릭 링크한다 (리포트용)."""
    destination_root.mkdir(parents=True, exist_ok=True)
    for source in source_dirs:
        source_path = Path(source)
        if not source_path.exists():
            continue
        link = destination_root / source_path.name
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(source_path.resolve())


def copy_top_structures(candidates: pd.DataFrame, destination_root: Path, id_column: str = "design_id") -> None:
    destination_root.mkdir(parents=True, exist_ok=True)
    for _, row in candidates.iterrows():
        source_dir = Path(str(row.get("pred_dir", "")))
        if not source_dir.exists():
            continue
        destination = destination_root / str(row[id_column])
        if destination.exists():
            continue
        try:
            shutil.copytree(source_dir, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns("boltz_out", "msas"))
        except Exception:  # noqa: BLE001
            continue


def series_sequences(candidates: pd.DataFrame, sequence_column: str = "sequence") -> dict[str, str]:
    id_column = "design_id" if "design_id" in candidates else "variant_id"
    identifiers = seq_utils.validate_candidate_ids(candidates[id_column].astype(str))
    return dict(zip(identifiers, candidates[sequence_column].astype(str)))


def fasta_to_candidates(fasta_path: str | Path, id_column: str = "design_id") -> pd.DataFrame:
    records = seq_utils.read_fasta(fasta_path)
    rows = [
        {id_column: seq_utils.validate_candidate_id(name), "sequence": seq_utils.clean_sequence(sequence), "source": "library"}
        for name, sequence in records.items()
    ]
    return pd.DataFrame(rows)
