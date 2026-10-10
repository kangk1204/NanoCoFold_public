"""Bounded first-stage storage with durable score receipts for pruned queries.

Completed score transactions, rather than deleted engine caches, authorize resume.
Only this helper's newly owned batch tree is pruned. Interrupted attempts have
an evidence checkpoint, no committed scores, and preserved engine outputs.
"""
from __future__ import annotations

import json
import inspect
import math
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pandas as pd

from . import colabfold, provenance
from .config import cfg_get, resolve_path
from .platform.paths import native
from .utils import seq
from .utils.shell import ExecutionCancelled, ToolError
from .utils.atomic import write_bytes


def _atomic(path, content):
    write_bytes(Path(path), content if isinstance(content, bytes) else content.encode())


def _json(path, value):
    _atomic(path, json.dumps(native(value), ensure_ascii=False, allow_nan=False, sort_keys=True).encode())


def _safe(stage, value):
    """Require lexical containment and reject symlinks before destructive work."""
    path = Path(value)
    if not path.is_absolute():
        path = stage / path
    if not path.is_relative_to(stage) or not path.resolve().is_relative_to(stage):
        raise ValueError("compact 출력은 현재 단계 소유 디렉터리 안에 있어야 합니다.")
    for parent in (path, *path.parents):
        if parent == stage.parent:
            break
        if parent.is_symlink():
            raise ValueError("compact 출력의 symlink는 지원하지 않습니다.")
    if path.is_dir() and any(p.is_symlink() for p in path.rglob("*")):
        raise ValueError("compact 출력 안의 symlink는 지원하지 않습니다.")
    return path


def _paths(row, run_root, *, absolute=False):
    result = dict(row)
    for key in ("pred_dir", "cif_path", "original_pred_dir", "original_cif_path"):
        value = result.get(key)
        if value:
            path = Path(value)
            path = path if path.is_absolute() else run_root / path
            result[key] = str(path) if absolute else str(path.relative_to(run_root))
    return result


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _error(value):
    return value not in (None, "", "nan", "None")


def _retain(state, identifiers, metric, top_n, id_column, controls=None):
    valid = [identifier for identifier in identifiers if identifier in state["completed"]
             and _finite(state["metrics"][identifier].get(metric))
             and not _error(state["metrics"][identifier].get("error"))]
    ascending = metric in {"interface_pae", "n_clashes"}
    valid.sort(key=lambda identifier: (state["metrics"][identifier][metric] * (1 if ascending else -1), identifier))
    controls = set(controls or {"WT"})
    retained = set([identifier for identifier in valid if identifier not in controls][:top_n])
    for identifier in controls:
        row = state["predictions"].get(identifier, {})
        if row.get("pred_dir"):
            retained.add(identifier)
    return retained


def _prune(stage, actions, commit):
    for relative in actions:
        path = _safe(stage, stage.parent / relative)
        if not path.is_relative_to(stage / "batches"):
            raise ValueError("compact는 소유한 batches 출력만 제거합니다.")
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        marker = path.parent / "colabfold_complete.json"
        if path.name == "colabfold_out" and marker.is_file():
            _safe(stage, marker)
            data = json.loads(marker.read_text())
            data.update(status="pruned", compact_commit=commit, prune_reason="outside_current_top_budget")
            _json(marker, data)


def _trim_commits(stage, current):
    """Keep two recoverable snapshots; receipt hashes remain append-only."""
    root = _safe(stage, stage / ".compact")
    commits = root / "commits"
    snapshots = []
    for snapshot in commits.iterdir():
        _safe(stage, snapshot)
        try:
            receipt = json.loads((snapshot / "receipt.json").read_text())
        except (OSError, ValueError):
            if snapshot.name == current:
                raise ValueError("현재 compact receipt가 없습니다.")
            # A crash can leave a snapshot that was never made authoritative.
            shutil.rmtree(snapshot)
            continue
        snapshots.append((receipt["sequence"], snapshot.name, snapshot))
    keep = {name for _, name, _ in sorted(snapshots, reverse=True)[:2]} | {current}
    for _, name, snapshot in snapshots:
        if name not in keep:
            shutil.rmtree(snapshot)


def _archive_batch_manifests(stage, state, commit):
    # Readers discover **/predictions.csv. Only the aggregate tombstone-aware
    # manifest should be current; preserve engine manifests as audit history.
    for offset in {row["offset"] for row in state["batches"]}:
        batch = _safe(stage, stage / "batches" / f"{offset:06d}")
        source = _safe(stage, batch / "predictions.csv")
        if source.is_file():
            history = _safe(stage, batch / ".history")
            history.mkdir(exist_ok=True)
            source.replace(history / ("predictions." + commit + ".csv"))


def _publish(stage, snapshot, pointer, manifest, state):
    for name in ("compact_scores.csv", "predictions.csv"):
        _atomic(stage / name, (snapshot / name).read_bytes())
    identifiers = [row["id"] for row in manifest["inputs"]["candidates"]]
    _json(stage / "prediction_plan.json", {"engine": "colabfold", "candidate_ids": identifiers,
                                           "compact": True, "fingerprint": manifest["fingerprint"]})
    _json(stage / "prediction_progress.json", {"completed": state["completed"], "compact": True,
                                               "fingerprint": manifest["fingerprint"]})
    stats = {"total": len(identifiers), "completed": len(state["completed"]),
             "attempted": len(state["metrics"]), "remaining": len(identifiers) - len(state["completed"]),
             "retained": len(state["retained"]),
             "pruned": sum(bool(row.get("structure_pruned")) for row in state["predictions"].values()),
             "batches": state["batches"], "wall_seconds": state["wall_seconds"],
             "fingerprint": manifest["fingerprint"], "score_commit": pointer["commit"]}
    _json(stage / "compact_progress.json", stats)
    _json(stage / "compact_checkpoint.json", {**pointer, "published": True})


def _commit(stage, manifest, state, identifiers, id_column):
    root = _safe(stage, stage / ".compact")
    commit = uuid.uuid4().hex
    snapshot = root / "commits" / commit
    snapshot.mkdir(parents=True)
    for name, key in (("predictions.csv", "predictions"), ("compact_scores.csv", "metrics")):
        rows = [state[key][identifier] for identifier in identifiers if identifier in state[key]]
        frame = pd.DataFrame(rows) if rows else pd.DataFrame(columns=[id_column])
        _atomic(snapshot / name, frame.to_csv(index=False))
    _json(snapshot / "state.json", state)
    receipt = {"fingerprint": manifest["fingerprint"], "commit": commit, "sequence": len(state["batches"]),
               "input_sequences_sha256": manifest["inputs"]["input_sequences_sha256"],
               "score_config_fingerprint": manifest["inputs"]["score_config_fingerprint"],
               "files": provenance.output_manifest(snapshot)}
    _json(snapshot / "receipt.json", receipt)
    _atomic(root / "receipts" / (commit + ".json"), (snapshot / "receipt.json").read_bytes())
    pointer = {"commit": commit, "receipt_sha256": provenance.file_digest(snapshot / "receipt.json"), "published": False}
    _json(stage / "compact_checkpoint.json", pointer)
    _publish(stage, snapshot, pointer, manifest, state)
    _archive_batch_manifests(stage, state, commit)
    _prune(stage, state["prune_actions"], commit)
    _trim_commits(stage, commit)


def _load(stage, manifest):
    pointer = json.loads((stage / "compact_checkpoint.json").read_text())
    commit = seq.validate_candidate_id(pointer["commit"])
    snapshot = _safe(stage, stage / ".compact" / "commits" / commit)
    receipt_path = snapshot / "receipt.json"
    if provenance.file_digest(receipt_path) != pointer["receipt_sha256"]:
        raise ValueError("compact receipt가 손상되었습니다. 원본을 보존하고 새 실행을 사용하세요.")
    receipt = json.loads(receipt_path.read_text())
    files = {k: v for k, v in provenance.output_manifest(snapshot).items() if k != "receipt.json"}
    if receipt["fingerprint"] != manifest["fingerprint"] or files != receipt["files"]:
        raise ValueError("compact 점수 원장이 손상되었거나 조건이 변경되었습니다.")
    if pointer.get("published"):
        for name in ("compact_scores.csv", "predictions.csv"):
            if not (stage / name).is_file() or provenance.file_digest(stage / name) != receipt["files"][name]:
                raise ValueError("compact 공개 점수 원장의 해시가 일치하지 않습니다.")
    state = json.loads((snapshot / "state.json").read_text())
    for record in state["retained_artifacts"].values():
        output = _safe(stage, stage.parent / record["path"])
        if not provenance.outputs_match(output, record["files"]):
            raise ValueError("compact 보존 구조가 손상되었습니다. 재사용을 중단합니다.")
    _publish(stage, snapshot, pointer, manifest, state)
    _archive_batch_manifests(stage, state, commit)
    _prune(stage, state["prune_actions"], commit)
    _trim_commits(stage, commit)
    return state


def _checkpoint_interrupted(stage, batch, manifest, state, linked, identifiers,
                            id_column, sequence_column, offset, started, exc):
    """Persist unscored evidence before propagating a stop; never prune it."""
    protected = set()
    for raw in linked.to_dict("records"):
        identifier = str(raw[id_column])
        row = _paths(native(raw), stage.parent)
        row["interrupted"] = True
        state["predictions"][identifier] = row
        state["metrics"][identifier] = {id_column: identifier, sequence_column: row[sequence_column],
            "error": f"interrupted_before_score: {type(exc).__name__}: {exc}"}
        value = row.get("pred_dir")
        if value:
            output = _safe(stage, stage.parent / value)
            if output.is_dir():
                files = provenance.output_manifest(output)
                if files:
                    state["retained_artifacts"][identifier] = {"path": value, "files": files}
                    protected.add(identifier)
    state["retained"] = sorted(set(state["retained"]) | protected)
    state["prune_actions"] = [value for value in state["prune_actions"]
        if not (stage.parent / value).is_relative_to(batch)]
    elapsed = time.monotonic() - started
    state["wall_seconds"] += elapsed
    state["batches"].append({"offset": offset, "attempted": len(linked), "completed": 0,
                            "wall_seconds": elapsed, "status": "interrupted"})
    _commit(stage, manifest, state, identifiers, id_column)


def predict_and_score(cfg, stage, target, candidates, predict, score,
                      id_column="design_id", sequence_column="sequence"):
    """Return all sequence/score records, keeping top-N structures plus WT.

    Conditions/ledger corruption fail closed; select a new run to change them.
    The callbacks use the existing screen.predict_complexes/_score_candidates
    contracts. Partial failures stay in the ledger and are retryable on resume.
    """
    stage = Path(stage).absolute()
    if stage.is_symlink():
        raise ValueError("compact 단계 symlink는 지원하지 않습니다.")
    stage = stage.resolve()
    options = colabfold.settings(cfg)
    retention = cfg_get(cfg, "screen.cascade", {}) or {}
    if any(type(retention.get(key, 0)) is not int or retention.get(key, 0) != 0 for key in ("diversity_n", "audit_n")):
        raise ValueError("compact 저장은 diversity_n=audit_n=0만 지원합니다.")
    top_n = retention.get("top_n", 99)
    if type(top_n) is not int or not 1 <= top_n <= 10000:
        raise ValueError("compact top_n은 1–10000 정수입니다.")
    metric = retention.get("ranking_metric", "iptm")
    if metric not in ("iptm", "ipsae", "interface_plddt", "interface_pae", "n_clashes"):
        raise ValueError("compact ranking_metric 오류")
    candidates = candidates.copy()
    candidates[id_column] = candidates[id_column].map(lambda v: seq.validate_candidate_id(str(v)))
    if candidates.empty or candidates[id_column].duplicated().any():
        raise ValueError("compact 후보 ID가 비어 있거나 중복됩니다.")
    identifiers = candidates[id_column].tolist()
    from .cascade import control_ids
    controls = control_ids(candidates, id_column)
    from .diagnostics import _engine_extra_env
    runtime = {} if cfg_get(cfg, "run.mock", False) else provenance.runtime_identity(
        cfg, "colabfold", env=resolve_path(cfg, options.get("env", "envs/colabfold")),
        env_vars=_engine_extra_env(cfg, "colabfold"))
    sequences = [{"id": row[id_column], "sequence": row[sequence_column]} for row in candidates.to_dict("records")]
    inputs = native({"candidates": sequences, "input_sequences_sha256": provenance.fingerprint({"rows": sequences}),
                     "candidate_metadata_sha256": provenance.fingerprint({"rows": native(candidates.to_dict("records"))}),
                     "target": target, "settings": options, "runtime": runtime,
                     "screen_config": cfg.get("screen", {}), "structure_config": cfg.get("structure", {}),
                     "seed": cfg_get(cfg, "run.seed", 42), "mock": cfg_get(cfg, "run.mock", False),
                     "score_config": cfg.get("score", {}),
                     "score_config_fingerprint": provenance.fingerprint(cfg.get("score", {})),
                     "adapter_sha256": provenance.file_digest(Path(__file__)),
                     "score_source_sha256": provenance.file_digest(Path(inspect.getsourcefile(score)))
                         if inspect.getsourcefile(score) and Path(inspect.getsourcefile(score)).is_file() else None,
                     "retention": {"top_n": top_n, "ranking_metric": metric, "always_wt": True,
                                   "control_ids": sorted(controls),
                                   "diversity_n": 0, "audit_n": 0}})
    manifest = {"schema_version": 1, "inputs": inputs, "fingerprint": provenance.fingerprint(inputs)}
    stage.mkdir(parents=True, exist_ok=True)
    _safe(stage, stage / "batches")
    _safe(stage, stage / ".compact")
    manifest_path = stage / "compact_manifest.json"
    if manifest_path.exists():
        if json.loads(manifest_path.read_text()) != manifest:
            raise ValueError("compact 실행 조건이 변경되었습니다. 이전 결과를 보존하고 새 실행을 생성하세요.")
    else:
        if any((stage / name).exists() for name in ("batches", ".compact", "predictions.csv", "compact_checkpoint.json", "compact_scores.csv")):
            raise ValueError("기존 출력은 compact 소유가 아닙니다. 새 실행이 필요합니다.")
        _json(manifest_path, manifest)
    state = {"predictions": {}, "metrics": {}, "completed": [], "retained": [],
             "retained_artifacts": {}, "prune_actions": [], "batches": [], "wall_seconds": 0.0}
    if (stage / "compact_checkpoint.json").exists():
        state = _load(stage, manifest)
    for offset in range(0, len(candidates), options["batch_size"]):
        batch = _safe(stage, stage / "batches" / f"{offset:06d}")
        pool = candidates.iloc[offset:offset + options["batch_size"]]
        pending = pool[~pool[id_column].isin(state["completed"])]
        if pending.empty:
            continue
        batch.mkdir(parents=True, exist_ok=True)
        old_manifest = _safe(stage, batch / "predictions.csv")
        if old_manifest.exists():
            history = _safe(stage, batch / ".history")
            history.mkdir(exist_ok=True)
            old_manifest.replace(history / ("predictions." + uuid.uuid4().hex + ".csv"))
        started = time.monotonic()
        interrupted = None
        try:
            predictions = predict(cfg, batch, target, pending, engine="colabfold",
                                  mock=bool(cfg_get(cfg, "run.mock", False)), dry_run=False,
                                  id_column=id_column, sequence_column=sequence_column)
        except (ExecutionCancelled, ToolError, TimeoutError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            interrupted = exc
            path = _safe(stage, batch / "predictions.csv")
            predictions = pd.read_csv(path, dtype={id_column: str}) if path.is_file() else pd.DataFrame(columns=[id_column])
            if "pred_dir" in predictions:
                predictions["pred_dir"] = predictions["pred_dir"].map(
                    lambda value: str(batch.parent / value) if isinstance(value, str) and not Path(value).is_absolute() else value)
        if id_column not in predictions or predictions[id_column].duplicated().any() or not set(predictions[id_column]).issubset(set(pending[id_column])):
            raise ValueError("compact 배치 예측 ID가 손상되었습니다.")
        linked = pending.drop(columns=[c for c in predictions if c != id_column and c in pending]).merge(predictions, on=id_column, how="left", validate="one_to_one")
        for row in linked.to_dict("records"):
            value = row.get("pred_dir")
            if isinstance(value, str) and value:
                output = _safe(stage, Path(value))
                if not output.is_relative_to(batch) or output == batch:
                    raise ValueError("compact 예측 경로는 현재 배치 내부여야 합니다.")
        if interrupted and not isinstance(interrupted, ToolError):
            _checkpoint_interrupted(stage, batch, manifest, state, linked, identifiers,
                                    id_column, sequence_column, offset, started, interrupted)
            raise interrupted
        try:
            metrics = score(cfg, linked, target, id_column)
        except (ExecutionCancelled, TimeoutError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            _checkpoint_interrupted(stage, batch, manifest, state, linked, identifiers,
                                    id_column, sequence_column, offset, started, exc)
            raise
        if id_column not in metrics or metrics[id_column].duplicated().any() or not set(metrics[id_column]).issubset(set(pending[id_column])):
            raise ValueError("compact 배치 점수 ID가 손상되었습니다.")
        scored = {str(row[id_column]): native(row) for row in metrics.to_dict("records")}
        for raw in linked.to_dict("records"):
            identifier = str(raw[id_column])
            row = native(raw)
            measurement = scored.get(identifier, {id_column: identifier, "error": "compact 점수가 반환되지 않았습니다."})
            measurement[sequence_column] = row[sequence_column]
            if _error(row.get("error")):
                measurement["error"] = row["error"]
            if measurement.get("cif_path"):
                _safe(stage, Path(measurement["cif_path"]))
            state["predictions"][identifier] = _paths(row, stage.parent)
            state["metrics"][identifier] = _paths(measurement, stage.parent)
            if not _error(measurement.get("error")) and row.get("pred_dir") and _finite(measurement.get(metric)):
                if identifier not in state["completed"]:
                    state["completed"].append(identifier)
        retained = _retain(state, identifiers, metric, top_n, id_column, controls)
        artifacts = {}
        for identifier, row in state["predictions"].items():
            output_value = row.get("pred_dir")
            if not output_value:
                continue
            output = _safe(stage, stage.parent / output_value)
            if identifier in retained:
                hashes = provenance.output_manifest(output)
                if not hashes:
                    if identifier not in controls or not _error(row.get("error")):
                        raise ValueError("compact 보존 구조 파일이 없습니다.")
                else:
                    artifacts[identifier] = {"path": output_value, "files": hashes}
                row["structure_pruned"] = False
            else:
                state["prune_actions"].append(output_value)
                row.update(original_pred_dir=output_value, pred_dir=None, structure_pruned=True,
                           prune_reason="outside_current_top_budget" if identifier in state["completed"] else "failed_first_stage")
                measurement = state["metrics"][identifier]
                if measurement.get("cif_path"):
                    measurement["original_cif_path"] = measurement["cif_path"]
                    measurement["cif_path"] = None
        for raw in batch.glob("_batches/*/raw"):
            state["prune_actions"].append(str(_safe(stage, raw).relative_to(stage.parent)))
        retained_paths = {record["path"] for record in artifacts.values()}
        state["prune_actions"] = sorted(set(state["prune_actions"]) - retained_paths)
        state["retained"] = sorted(retained)
        state["retained_artifacts"] = artifacts
        state["wall_seconds"] += time.monotonic() - started
        state["batches"].append({"offset": offset, "attempted": len(pending),
                                 "completed": sum(identifier in state["completed"] for identifier in pending[id_column]),
                                 "wall_seconds": time.monotonic() - started})
        _commit(stage, manifest, state, identifiers, id_column)
        if interrupted and not isinstance(interrupted, ToolError):
            raise interrupted
    predictions = pd.DataFrame([_paths(state["predictions"][identifier], stage.parent, absolute=True)
                                for identifier in identifiers if identifier in state["predictions"]])
    metrics = pd.DataFrame([_paths(state["metrics"][identifier], stage.parent, absolute=True)
                           for identifier in identifiers if identifier in state["metrics"]])
    return predictions, metrics
