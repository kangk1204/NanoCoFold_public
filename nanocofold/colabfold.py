"""Batched, resumable AlphaFold-Multimer screening with explicit local weights."""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

from . import provenance
from .config import cfg_get, resolve_path
from .utils import pdb_io, seq, shell
from .utils.tool_args import validate_colabfold_extra_args


def _save(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def settings(cfg: dict) -> dict:
    raw = cfg_get(cfg, "screen.colabfold", {}) or {}
    if not isinstance(raw, dict):
        raise ValueError("screen.colabfold must be a settings object")
    validate_colabfold_extra_args(raw.get("extra_args"))
    values = {"num_recycles": 1, "num_models": 1, "num_seeds": 1, "batch_size": 32,
              "fast_kernels": True, "msa_mode": "precomputed", "msa_dir": None,
              "msa_max_sequences": 2048, **raw}
    for key, low, high in (("num_recycles", 1, 20), ("num_models", 1, 5),
                          ("num_seeds", 1, 10), ("batch_size", 1, 512), ("msa_max_sequences", 2, 10000)):
        value = values[key]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"screen.colabfold.{key}: {low}–{high} 정수가 필요합니다.")
    if type(values["fast_kernels"]) is not bool:
        raise ValueError("screen.colabfold.fast_kernels는 true/false입니다.")
    if not isinstance(values["msa_mode"], str) or values["msa_mode"] not in {"precomputed", "single_sequence"}:
        raise ValueError("screen.colabfold.msa_mode must be precomputed (legacy single_sequence is read/mock only)")
    if values["msa_dir"] is not None and not isinstance(values["msa_dir"], (str, Path)):
        raise ValueError("screen.colabfold.msa_dir must be a path or null")
    return values


def _normalize(raw: Path, identifier: str, destination: Path, chains: dict) -> int:
    """Only complete, sequence-matched model/score pairs become reusable outputs."""
    from .score_complex import validate_pae
    structures = sorted(raw.glob(identifier + "_unrelaxed_rank_*.pdb"))
    if not structures:
        raise ValueError("ColabFold 구조가 없습니다. 배치 로그와 GPU 메모리를 확인하세요.")
    destination.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(structures):
        suffix = source.name.removeprefix(identifier + "_unrelaxed_").removesuffix(".pdb")
        score_path = raw / (identifier + "_scores_" + suffix + ".json")
        payload = json.loads(score_path.read_text())
        if not isinstance(payload, dict):
            raise ValueError("ColabFold 점수 JSON이 객체가 아닙니다.")
        structure = pdb_io.load_structure(source)
        models = list(structure.get_models())
        if not models:
            raise ValueError("ColabFold 출력 PDB에 모델이 없습니다.")
        actual = {chain.id: pdb_io.chain_sequence(chain) for chain in models[0]}
        if actual != chains:
            raise ValueError("ColabFold 출력 체인·서열이 입력과 다릅니다.")
        pae = np.asarray(payload.get("pae"), dtype=np.float32)
        validate_pae(pae, sum(map(len, chains.values())))
        plddt = np.asarray(payload.get("plddt"), dtype=np.float32)
        if plddt.shape != (pae.shape[0],) or not np.isfinite(plddt).all() or np.any((plddt < 0) | (plddt > 100)):
            raise ValueError("ColabFold pLDDT 크기·값 오류")
        confidence = {}
        for key in ("iptm", "ptm"):
            value = payload.get(key)
            if type(value) not in (int, float) or not np.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"ColabFold {key}가 누락되었거나 유효하지 않습니다.")
            confidence[key] = float(value)
        stem = f"colabfold_model_{index}"
        shutil.copyfile(source, destination / (stem + ".pdb"))
        shutil.copyfile(score_path, destination / ("raw_scores_" + stem + ".json"))
        confidence.update(plddt=float(plddt.mean()), source="colabfold", raw_model=source.name,
                          rank=index + 1, structure_model="alphafold2_multimer_v3")
        _save(destination / ("confidence_" + stem + ".json"), confidence)
        np.savez_compressed(destination / ("pae_" + stem + ".npz"), pae=pae)
    return len(structures)


def _valid_outputs(output: Path, chains: dict, expected_models: int) -> bool:
    from .score_complex import load_pae, validate_pae, load_confidence
    try:
        models = sorted(output.glob("colabfold_model_*.pdb"))
        if len(models) != expected_models:
            return False
        for model in models:
            actual = {chain.id: pdb_io.chain_sequence(chain) for chain in next(pdb_io.load_structure(model).get_models())}
            if actual != chains:
                return False
            from .score_complex import PredictionFiles
            pred = PredictionFiles(model, confidence_json=output / ("confidence_" + model.stem + ".json"),
                                   pae_npz=output / ("pae_" + model.stem + ".npz"), source="colabfold")
            validate_pae(load_pae(pred), sum(map(len, chains.values())))
            if any(not np.isfinite(load_confidence(pred).get(key, np.nan)) for key in ("iptm", "ptm")):
                return False
        return True
    except (ValueError, OSError, TypeError, AttributeError, StopIteration):
        return False


def predict(cfg, work_dir, target, candidates, *, mock=False, dry_run=False,
            id_column="design_id", sequence_column="sequence", chain_records=None):
    from .screen import complex_chain_records, _predictions_manifest_frame
    from .diagnostics import _engine_extra_env
    from .input_review import validate_reviewed_input
    validate_reviewed_input(cfg)
    work_dir = Path(work_dir)
    identifiers = seq.validate_candidate_ids(candidates[id_column].astype(str))
    aliases = seq.candidate_id_aliases(identifiers)
    work_dir.mkdir(parents=True, exist_ok=True)
    if candidates[id_column].astype(str).duplicated().any():
        raise ValueError("ColabFold 후보 ID는 중복될 수 없습니다.")
    options = settings(cfg)
    msa_collection = None
    if not mock and not dry_run:
        if options["msa_mode"] != "precomputed":
            raise ValueError("Real ColabFold inference requires validated precomputed MSA; single_sequence is legacy read/mock only")
        from .colabfold_msa import from_config
        msa_collection = from_config(cfg)
    if bool(cfg_get(cfg, "screen.restraint.enabled", False)):
        raise ValueError("ColabFold는 이 플랫폼의 접촉 제약을 지원하지 않습니다. restraint를 끄고 결과에서 접촉을 검토하세요.")
    seed = int(cfg_get(cfg, "run.seed", 42))
    env = resolve_path(cfg, options.get("env", "envs/colabfold"))
    data = resolve_path(cfg, options.get("data_dir", "models/colabfold"))
    extra = _engine_extra_env(cfg, "colabfold")
    expected_models = options["num_models"] * options["num_seeds"]
    identity = {} if mock or dry_run else provenance.runtime_identity(cfg, "colabfold", env=env, env_vars=extra)
    marker_name = "colabfold_complete.json"
    attempt = uuid.uuid4().hex
    plan = {"attempt_id": attempt, "engine": "colabfold", "candidate_ids": identifiers, "id_aliases": aliases}
    progress = {"attempt_id": attempt, "completed": []}
    prepared = []
    # Validate the complete submission before changing any saved output/cache.
    for _, row in candidates.iterrows():
        identifier = seq.validate_candidate_id(str(row[id_column]))
        if chain_records is None:
            chains, auxiliary, binder = complex_chain_records(target, str(row[sequence_column]))
        else:
            from .complex import proteins
            validated = proteins([{"sequence": value} for value in chain_records.values()])
            chains = {p["id"]: p["sequence"] for p in validated}
            if list(chains.items()) != list(chain_records.items()):
                raise ValueError("ColabFold 일반 복합체 체인 순서·서열 불일치")
            auxiliary, binder = [], next(reversed(chains))
        if sum(map(len, chains.values())) > 2000 or len(chains) > 25:
            raise ValueError("ColabFold 복합체 상한은 2,000잔기·25체인입니다.")
        query_msa = msa_collection.for_query(identifier, row, chains, binder, cfg) if msa_collection else None
        prepared.append((identifier, chains, auxiliary, binder, query_msa))
    if not dry_run:
        _save(work_dir / "prediction_plan.json", plan)
        _save(work_dir / "prediction_progress.json", progress)
    pending, records = [], {}
    for identifier, chains, auxiliary, binder, query_msa in prepared:
        directory = work_dir / identifier
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / "colabfold_out"
        payload = {"schema_version": 2, "chains": chains, "seed": seed, "settings": options, "runtime": identity,
                   "msa": query_msa.identity if query_msa else {"mode": "mock" if mock else options["msa_mode"]}}
        fingerprint = provenance.fingerprint(payload)
        record = {id_column: identifier, "pred_dir": str(output.resolve()), "engine": "mock" if mock else "colabfold",
                  "target_chain": "A", "binder_chain": binder, "seed": seed, "msa": "mock" if mock else options["msa_mode"],
                  "restraint": False, "num_recycles": options["num_recycles"], "num_models": expected_models,
                  "aux_chains": ";".join(f"{name}:{letter}" for name, letter in auxiliary)}
        record["native_job_id"] = aliases[identifier]
        records[identifier] = record
        if query_msa:
            record["msa_sha256"] = query_msa.identity["query_a3m_sha256"]
            record["msa_parent_id"] = query_msa.identity.get("parent_id", "")
        try:
            marker = json.loads((directory / marker_name).read_text())
        except (OSError, ValueError):
            marker = {}
        if not mock and not dry_run and marker.get("fingerprint") == fingerprint and marker.get("status") == "complete" and provenance.outputs_match(output, marker.get("outputs")) and _valid_outputs(output, chains, expected_models):
            # A legacy completion may predate job aliases. The new plan's alias
            # must not be presented as the filename used by an earlier call.
            record["native_job_id"] = marker.get("native_job_id")
            progress["completed"].append(identifier)
        else:
            if not dry_run:
                provenance.archive_existing([output, directory / marker_name], directory)
            pending.append((identifier, chains, fingerprint, query_msa))
    def publish():
        if dry_run:
            return
        _save(work_dir / "prediction_progress.json", progress)
        frame = pd.DataFrame([records[x] for x in identifiers])
        _predictions_manifest_frame(frame, work_dir.parent.resolve()).to_csv(work_dir / "predictions.csv", index=False)
    publish()
    if mock:
        from .mock import make_mock_chai_output
        for identifier, chains, fingerprint, query_msa in pending:
            output = work_dir / identifier / "colabfold_out"
            make_mock_chai_output(output, identifier, chains["A"], chains[records[identifier]["binder_chain"]], [], seed=seed)
            progress["completed"].append(identifier)
        publish()
        return pd.DataFrame([records[x] for x in identifiers])
    for offset in range(0, len(pending), options["batch_size"]):
        chunk = pending[offset:offset + options["batch_size"]]
        batch = work_dir / "_batches" / f"{attempt}_{offset:06d}"
        batch.mkdir(parents=True, exist_ok=True)
        query = batch / "queries.csv"
        query_rows = []
        for identifier, chains, fingerprint, query_msa in chunk:
            item = {"id": aliases[identifier], "sequence": ":".join(chains.values())}
            if query_msa:
                path = query_msa.write(batch / "a3ms" / (aliases[identifier] + ".a3m"))
                item["a3mpath"] = str(path.resolve())
                _save(work_dir / identifier / "colabfold_msa.json", query_msa.identity)
            query_rows.append(item)
        pd.DataFrame(query_rows).to_csv(query, index=False)
        _save(batch / "query_id_aliases.json", {"schema_version": 1,
            "original_to_alias": {identifier: aliases[identifier] for identifier, *_ in chunk},
            "alias_to_original": {aliases[identifier]: identifier for identifier, *_ in chunk}})
        raw = batch / "raw"
        argv = [str(shell.env_executable(env, "colabfold_batch", dry_run=dry_run)), str(query), str(raw),
                "--model-type", "alphafold2_multimer_v3", "--msa-mode", "mmseqs2_uniref_env" if options["msa_mode"] == "precomputed" else "single_sequence", "--data", str(data),
                "--num-models", str(options["num_models"]), "--num-seeds", str(options["num_seeds"]),
                "--num-recycle", str(options["num_recycles"]), "--random-seed", str(seed), "--rank", "iptm",
                "--sort-queries-by", "length", "--recompile-padding", "10"]
        if options["msa_mode"] == "precomputed":
            # Supplied A3M paths bypass service search in pinned ColabFold 1.6.3.
            # single_sequence would discard even supplied A3M homologs upstream.
            argv += ["--pair-mode", "unpaired"]
        if options["fast_kernels"]:
            argv.append("--use-fast-kernels")
        started = time.monotonic()
        interrupted = None
        try:
            result = shell.run_in_env(env, argv, extra_env=extra, log_path=batch / "colabfold.log", check=False, dry_run=dry_run)
        except BaseException as exc:
            interrupted = exc
            result = None
        if not dry_run:
            for identifier, chains, fingerprint, query_msa in chunk:
                directory = work_dir / identifier
                done = raw / (aliases[identifier] + ".done.txt")
                if interrupted is not None and (not done.is_file() or done.is_symlink()):
                    # Upstream v1.6.3 writes this candidate-local marker only
                    # after saving all models, scores and plots. Parseable raw
                    # files alone remain insufficient after cancellation.
                    records[identifier]["error"] = f"{type(interrupted).__name__}: {interrupted}"
                    _save(directory / marker_name, {"status": "interrupted", "fingerprint": fingerprint,
                                                   "error": records[identifier]["error"]})
                    continue
                try:
                    if chain_records is not None and (result is None or result.returncode != 0):
                        raise ValueError("ColabFold 일반 복합체 명령이 정상 종료하지 않았습니다. 원본 출력과 로그를 확인하세요.")
                    count = _normalize(raw, aliases[identifier], directory / "colabfold_out", chains)
                    if count != expected_models:
                        raise ValueError(f"ColabFold 모델 수 불일치: {count}/{expected_models}")
                    marker = {"status": "complete", "fingerprint": fingerprint,
                              "original_id": identifier, "native_job_id": aliases[identifier],
                              "outputs": provenance.output_manifest(directory / "colabfold_out"), "model_count": count,
                              "msa": query_msa.identity if query_msa else None}
                    if done.is_file() and not done.is_symlink():
                        marker["native_completion"] = {"path": str(done.relative_to(work_dir)),
                                                       "sha256": provenance.file_digest(done),
                                                       "source": "ColabFold v1.6.3 candidate .done.txt"}
                    if interrupted is not None:
                        marker["batch_interrupted"] = {"error_type": type(interrupted).__name__, "error": str(interrupted)}
                    progress["completed"].append(identifier)
                    records[identifier].pop("error", None)
                except Exception as exc:
                    records[identifier]["error"] = f"{type(exc).__name__}: {exc}"
                    marker = {"status": "interrupted" if interrupted is not None else "failed",
                              "fingerprint": fingerprint, "error": records[identifier]["error"]}
                _save(directory / marker_name, marker)
            _save(batch / "timing.json", {"wall_seconds": time.monotonic() - started, "queries": len(chunk),
                                          "candidate_ids": [entry[0] for entry in chunk],
                                          "returncode": result.returncode if result else None})
            publish()
        if interrupted:
            raise interrupted
    return pd.DataFrame([records[x] for x in identifiers])
