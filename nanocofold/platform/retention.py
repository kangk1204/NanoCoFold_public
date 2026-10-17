"""Explicit, hash-reviewed reduction of terminal runs; no inference or shared cleanup.

Planning is read-only. A flush retains every result row and final coordinate,
while MSA/PAE tensors and owned stage intermediates can be removed. Unknown
files are kept. The compact archive is published and verified before unlinking
any file; a durable receipt permits a failed deletion to be retried safely.
Only owned local run files are reduced. Sites/R2 objects, shared caches and
weights are never deleted; a pending cloud publication blocks local reduction.
"""
from __future__ import annotations

import contextlib
import copy
import fcntl
import gzip
import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import time
import uuid
from pathlib import Path

from .paths import native

SCHEMA_VERSION = 1
ARCHIVE = "retained"
TERMINAL = {"complete", "partial_failure"}
STAGES = {"01_msa", "01_target", "02_design", "02_comparison", "02_screen",
          "02b_tier2", "02c_boltz2", "02_mature", "02b_mature_complex",
          "02_complex", "03_score", "04_developability", "05_final"}
ROOT_KEEP = {"run_config.yaml", "run_state.json", "platform_status.json",
             "platform_manifest.json", "manifest.json", "input_review.json",
             "input_reference_manifest.json", "residue_mapping.csv",
             "complex_results.json", "models.csv", "preflight.json"}
ROOT_DISPOSABLE = {"report.html", "report_offline.html", "report_delivery.zip",
                   "result_package.zip", "results.zip"}
PROTECTED_DIRS = {"cache", "caches", "weights", "models", "database", "databases",
                  "environments", ".history"}
CAPABILITIES = {"metrics": True, "candidate_sequences": True, "inputs": True,
                "all_final_structures": True, "pae": False, "msa": False,
                "resume_intermediates": False, "full_native_outputs": False}


class RetentionError(ValueError):
    """The reviewed deletion cannot be shown to be safe."""


def _encoded(value):
    return json.dumps(native(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _safe_relative(value):
    path = Path(value)
    if path.is_absolute() or not path.parts or any(p in {"..", ".", ""} for p in path.parts):
        raise RetentionError("상대 파일 경로가 잘못되었습니다.")
    return path


def _checked_directory(path):
    """Reject links in every existing path component, including the workspace."""
    path = Path(os.path.abspath(path))
    for current in (path, *path.parents):
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise RetentionError(
                "보존·결과 등록 경로에 심볼릭 링크가 있습니다. 작업 공간에서 pwd -P로 실제 경로를 확인해 다시 지정하세요. "
                "macOS /var/folders는 /private/var/folders를 사용하세요.")
        if not stat.S_ISDIR(info.st_mode):
            raise RetentionError("보존 작업은 실제 디렉터리에만 적용할 수 있습니다.")
    if path.stat().st_uid != os.getuid():
        raise RetentionError("다른 사용자의 작업은 정리할 수 없습니다.")
    return path


@contextlib.contextmanager
def _file(root, relative):
    """Open each component without following a replaced directory or file link."""
    parts = _safe_relative(relative).parts
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise RetentionError("파일 소유자·형식·하드링크가 안전하지 않습니다.")
            with os.fdopen(fd, "rb") as handle:
                fd = -1
                yield handle, info
        finally:
            if fd >= 0:
                os.close(fd)
    finally:
        os.close(descriptor)


def _bytes(root, relative):
    with _file(root, relative) as (handle, _):
        return handle.read()


def _receipt(root, relative):
    with _file(root, relative) as (handle, info):
        sha = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
        after = os.fstat(handle.fileno())
        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise RetentionError("확인 중 파일이 변경되었습니다.")
        return {"path": str(relative), "size": info.st_size, "sha256": sha.hexdigest(),
                "device": info.st_dev, "inode": info.st_ino, "mtime_ns": info.st_mtime_ns,
                "ctime_ns": info.st_ctime_ns}


def _content_receipt(root, relative):
    """Capture bytes and their receipt from one stable, no-follow descriptor."""
    with _file(root, relative) as (handle, info):
        content = handle.read()
        after = os.fstat(handle.fileno())
        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise RetentionError("입력 파일을 읽는 동안 내용이 변경되었습니다.")
        receipt = {"path": str(relative), "size": info.st_size, "sha256": _hash(content),
                   "device": info.st_dev, "inode": info.st_ino, "mtime_ns": info.st_mtime_ns,
                   "ctime_ns": info.st_ctime_ns}
        if len(content) != receipt["size"]:
            raise RetentionError("입력 파일 내용과 크기가 일치하지 않습니다.")
        return content, receipt


def _inventory(root, *, exclude=()):
    result = []
    def visit(folder):
        for entry in sorted(os.scandir(folder), key=lambda item: item.name):
            path = Path(entry.path)
            relative = path.relative_to(root)
            if relative.parts[0] in exclude or str(relative) == ".run.lock":
                continue
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise RetentionError(f"심볼릭 링크가 있어 정리를 차단했습니다: {relative}")
            if info.st_uid != os.getuid():
                raise RetentionError(f"다른 사용자의 파일입니다: {relative}")
            if stat.S_ISDIR(info.st_mode):
                visit(path)
            elif stat.S_ISREG(info.st_mode):
                result.append(_receipt(root, relative))
            else:
                raise RetentionError(f"일반 파일이 아닙니다: {relative}")
    visit(root)
    return result


def _json(root, relative):
    try:
        data = json.loads(_bytes(root, relative))
    except (ValueError, UnicodeError) as exc:
        raise RetentionError(f"JSON 기록이 손상되었습니다: {relative}") from exc
    if not isinstance(data, dict):
        raise RetentionError(f"JSON 객체가 필요합니다: {relative}")
    return data


def _atomic_json(path, payload):
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(_encoded(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _no_arrays(value):
    """PAE/large per-residue arrays are deliberately outside this retention tier."""
    if isinstance(value, dict):
        return {key: _no_arrays(item) for key, item in value.items()
                if key.lower() not in {"pae", "predicted_aligned_error", "aligned_confidence_probs",
                                       "plddt", "contact_probs", "distogram", "token_plddt"}
                or not isinstance(item, (list, dict))}
    if isinstance(value, list):
        return [_no_arrays(item) for item in value]
    return value


def _coordinate_payload(reader, row, observation_id):
    """Keep original coordinates and axes without loading native PAE tensors."""
    candidate_id = row["candidate_id"]
    if reader.config.get("run", {}).get("mode") == "complex":
        path = reader.artifact_path(row["structure_file"])
        payload = _json(reader.root, str(path.relative_to(reader.root)))
        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RetentionError("최종 복합체 좌표가 손상되었습니다.")
        payload["pae_status"] = "removed_by_retention" if payload.get("pae") else payload.get("pae_status", "unavailable")
        payload = _no_arrays(payload)
        payload.update(candidate_id=candidate_id, observation_id=observation_id,
                       original_path=str(path.relative_to(reader.root)))
        return payload
    from ..score_complex import PredictionFiles
    path = reader.artifact_path(row["cif_path"])
    # This private reader is used only by this planner. Its interpretation
    # still uses original coordinates, mappings, CDR/contact/reference data;
    # deliberately omit native tensors rather than materializing a full PAE.
    original = reader._prediction_for_path
    prediction = original(path)
    pae_status = "unavailable"
    # Inspect the NPZ member list, not its large arrays. A coordinate file
    # without a supplied PAE must never be relabelled as a computed PAE lost
    # during retention. ResultReader independently validates native arrays.
    import numpy as np
    for associated in (prediction.scores_npz, prediction.pae_npz):
        if associated and associated.is_file():
            with _file(reader.root, str(associated.relative_to(reader.root))) as (handle, _):
                try:
                    with np.load(handle, allow_pickle=False) as arrays:
                        if any(key in arrays.files for key in ("pae", "PAE")):
                            pae_status = "removed_by_retention"
                except (ValueError, OSError, EOFError):
                    pae_status = "unassessed"
    reader._prediction_for_path = lambda coordinate: PredictionFiles(coordinate)
    try:
        payload = reader.structure(candidate_id, observation_id)
    finally:
        reader._prediction_for_path = original
    if not payload.get("content") or not any(chain.get("residues") for chain in payload.get("chains", [])):
        raise RetentionError("최종 구조에 잔기가 없습니다.")
    payload["pae_status"] = payload.get("pae_status", pae_status)
    payload.update(original_path=str(path.relative_to(reader.root)))
    for key in ("seed", "base_seed", "seed_source", "engine_model_id", "engine", "structure_stage"):
        if key in row:
            payload[key] = row[key]
    return payload


def retained_record(run_root):
    """Return a verified archive, or None for a full run. Corruption never hides."""
    root = _checked_directory(run_root)
    archive = root / ARCHIVE
    if not archive.exists():
        return None
    _checked_directory(archive)
    manifest = _json(root, f"{ARCHIVE}/manifest.json")
    if manifest.get("schema_version") != SCHEMA_VERSION or not isinstance(manifest.get("files"), list):
        raise RetentionError("정리 보관본 manifest가 손상되었습니다.")
    names = []
    for entry in manifest["files"]:
        if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
                or type(entry.get("size")) is not int or entry["size"] < 0
                or not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64):
            raise RetentionError("정리 보관본 파일 manifest가 손상되었습니다.")
        relative = _safe_relative(entry["path"])
        if relative.parts[0] != ARCHIVE:
            raise RetentionError("정리 보관본 경로가 잘못되었습니다.")
        actual = _receipt(root, relative)
        if actual["size"] != entry["size"] or actual["sha256"] != entry["sha256"]:
            raise RetentionError("정리 보관본 해시가 일치하지 않습니다.")
        names.append(str(relative))
    if len(names) != len(set(names)) or f"{ARCHIVE}/record.json" not in names or f"{ARCHIVE}/plan.json" not in names:
        raise RetentionError("정리 보관본 필수 파일 manifest가 손상되었습니다.")
    record = _json(root, f"{ARCHIVE}/record.json")
    if (record.get("schema_version") != SCHEMA_VERSION
            or any(not isinstance(record.get(key), dict) for key in ("config", "state", "summary", "candidates", "observations", "provenance", "capabilities"))
            or not isinstance(record.get("structures"), list)
            or not isinstance(record["candidates"].get("rows"), list)
            or not isinstance(record["observations"].get("rows"), list)):
        raise RetentionError("정리 보관본 버전이 잘못되었습니다.")
    for structure in record["structures"]:
        if (not isinstance(structure, dict) or not isinstance(structure.get("candidate_id"), str)
                or structure.get("content_file") not in names
                or type(structure.get("content_size")) is not int or structure["content_size"] < 0
                or not isinstance(structure.get("content_sha256"), str) or len(structure["content_sha256"]) != 64):
            raise RetentionError("정리 보관본 구조 manifest가 손상되었습니다.")
    return record


def retained_structure(run_root, candidate_id, observation_id=None):
    record = retained_record(run_root)
    if record is None:
        raise FileNotFoundError("정리 보관본이 없습니다.")
    item = next((item for item in record["structures"] if item["candidate_id"] == candidate_id
                 and item.get("observation_id") == observation_id), None)
    if item is None:
        raise FileNotFoundError("보관된 구조가 없습니다.")
    payload = copy.deepcopy(item)
    if _safe_relative(payload["content_file"]).parts[0] != ARCHIVE:
        raise RetentionError("보관 좌표 경로가 잘못되었습니다.")
    data = gzip.decompress(_bytes(Path(run_root), payload["content_file"]))
    if len(data) != payload["content_size"] or _hash(data) != payload["content_sha256"]:
        raise RetentionError("보관 좌표 해시가 일치하지 않습니다.")
    payload["content"] = data.decode("utf-8")
    return payload


class RetentionManager:
    def __init__(self, workspace):
        self.workspace = _checked_directory(workspace)

    def _root(self, run_root):
        root = _checked_directory(run_root)
        runs = _checked_directory(self.workspace / "runs")
        if root.parent != runs:
            raise RetentionError("이 작업 공간의 runs 직속 결과만 정리할 수 있습니다.")
        return root

    @contextlib.contextmanager
    def _read_lock(self, root):
        path = root / ".run.lock"
        if not path.exists() and not path.is_symlink():
            yield
            return
        with _file(root, ".run.lock") as (handle, _):
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RetentionError("현재 실행 중인 작업은 정리할 수 없습니다.") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _inactive(self, root):
        from .jobs import process_stamp
        state = _json(root, "run_state.json")
        execution = state.get("execution", {})
        import yaml
        try:
            cfg = yaml.safe_load(_bytes(root, "run_config.yaml"))
        except yaml.YAMLError as exc:
            raise RetentionError("실행 설정을 확인할 수 없습니다.") from exc
        if (not isinstance(cfg, dict) or not isinstance(cfg.get("run"), dict)
                or not isinstance(state.get("phases", {}), dict)):
            raise RetentionError("완료 결과의 설정·상태 형식을 확인할 수 없습니다.")
        complex_run = cfg["run"].get("mode") == "complex"
        if (not isinstance(execution, dict) or execution.get("state") not in TERMINAL
                or not complex_run and "final" not in state.get("phases", {})):
            raise RetentionError("완료·검증된 결과만 정리할 수 있습니다. 실행 또는 불명확한 상태입니다.")
        from ..publication import publication_issue, tables_available
        final = state.get("phases", {}).get("final", {})
        issue = publication_issue(final)
        if issue or final.get("table_sha256") is not None and not tables_available(root, final):
            raise RetentionError("산출물 발행 완료를 확인한 결과만 정리할 수 있습니다: " + (issue or "계산 표 해시 불일치"))
        def check_process(pid, stamp=None):
            if pid is None:
                return
            if type(pid) is not int or pid <= 0:
                raise RetentionError("실행 프로세스 기록을 확인할 수 없습니다.")
            current = process_stamp(pid)
            if current and (not stamp or str(current) == str(stamp)):
                raise RetentionError("소유 작업 프로세스가 아직 실행 중입니다.")
        check_process(execution.get("pid"), execution.get("process_stamp"))
        db = self.workspace / ".nanocofold/jobs.sqlite3"
        if not db.exists():
            return
        _checked_directory(db.parent)
        if db.is_symlink():
            raise RetentionError("작업 DB의 경로를 확인할 수 없습니다.")
        try:
            with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as connection:
                # Check terminal ownership and pending cloud publication from
                # one read snapshot. Cloud exporters do not hold RunLock yet.
                connection.execute("BEGIN")
                jobs = [json.loads(row[0]) for row in connection.execute(
                    "SELECT payload FROM jobs WHERE json_extract(payload,'$.run_dir')=?", (str(root),))]
                sync_table = connection.execute(
                    "SELECT type FROM sqlite_master WHERE name='cloud_sync'").fetchone()
                pending_sync = False
                if sync_table:
                    if sync_table[0] != "table":
                        raise RetentionError("클라우드 동기화 기록 형식을 확인할 수 없습니다.")
                    pending_sync = connection.execute(
                        "SELECT 1 FROM cloud_sync AS sync JOIN jobs AS job ON job.id=sync.remote_id "
                        "WHERE json_extract(job.payload,'$.run_dir')=? "
                        "AND (sync.result_synced IS NULL OR sync.result_synced!=1) LIMIT 1",
                        (str(root),)).fetchone() is not None
        except (sqlite3.Error, ValueError) as exc:
            raise RetentionError("현재 작업 DB를 확인할 수 없습니다.") from exc
        if pending_sync:
            raise RetentionError("클라우드 결과 동기화가 완료되지 않아 정리를 차단했습니다. 업로드 완료 후 다시 확인하세요.")
        for job in jobs:
            value = job.get("run_dir")
            if not value or Path(os.path.abspath(value)) != root:
                continue
            if job.get("state") not in TERMINAL:
                raise RetentionError("활성·중단·미확인 작업은 정리할 수 없습니다.")
            check_process(job.get("pid"), job.get("process_stamp"))
            for pid, stamp in job.get("descendants", []):
                check_process(pid, stamp)

    @staticmethod
    def _keep(entry):
        path = Path(entry["path"])
        if any(part.lower() in PROTECTED_DIRS for part in path.parts[:-1]):
            return True
        if path.name in ROOT_KEEP or path.suffix.lower() in {".csv", ".fasta", ".fa", ".faa", ".yaml", ".yml"}:
            return True
        if len(path.parts) == 1 and (path.name == "report_explorer.html" or
                re.fullmatch(r".+_explorer-[a-f0-9]{64}\.html", path.name)):
            return False
        if path.name.startswith("input_reference") or "manifest" in path.name or path.name.endswith(("receipt.json", "status.json", "state.json")):
            return True
        if path.parts[0] == ARCHIVE:
            return True
        return not (path.parts[0] in STAGES or str(path) in ROOT_DISPOSABLE)

    def _snapshot(self, root, inventory):
        from .results import ResultReader
        reader = ResultReader(root)
        candidates = reader.candidates(limit=None)
        summary = reader.summary()
        observations = reader.observations()
        if reader.issues or summary.get("status") not in TERMINAL:
            raise RetentionError("결과 파일 검증에 실패해 정리를 차단했습니다: " + "; ".join(reader.issues))
        by_id = {row["candidate_id"]: row for row in candidates["rows"]}
        # Snapshot the interpreted rows once. Coordinate interpretation uses
        # these exact rows, avoiding a repeated whole-result CSV traversal.
        reader.all_rows = lambda: candidates["rows"]
        reader.observations = lambda candidate_id=None: {
            **observations, "rows": [row for row in observations["rows"]
                                     if candidate_id is None or row["candidate_id"] == candidate_id]}
        structures, files = [], {}
        model_rows = [(row, None) for row in candidates["rows"] if row.get("structure_available")]
        for row in observations["rows"]:
            if reader.config.get("run", {}).get("mode") == "complex" and row.get("structure_available"):
                model_rows.append((row, row["observation_id"]))
                continue
            if row.get("structure_pruned") or not row.get("cif_path"):
                continue
            model_rows.append(({**by_id.get(row["candidate_id"], {}), **row}, row["observation_id"]))
        for row, observation_id in model_rows:
            payload = _coordinate_payload(reader, row, observation_id)
            content = payload.pop("content").encode("utf-8")
            sha = _hash(content)
            extension = "pdb" if payload.get("format") == "pdb" else "cif"
            relative = f"{ARCHIVE}/structures/{sha}.{extension}.gz"
            files[relative] = gzip.compress(content, compresslevel=9, mtime=0)
            payload.update(content_file=relative, content_sha256=sha, content_size=len(content),
                           pae=None)
            structures.append(_no_arrays(payload))
        # Keep standalone sequence-bearing/configuration files by hash as well
        # as the typed result tables, preserving failed and unassessed rows.
        sequence_files = {}
        for entry in inventory:
            path = Path(entry["path"])
            if path.suffix.lower() in {".fasta", ".fa", ".faa"}:
                sequence_files[str(path)] = _bytes(root, path).decode("utf-8")
        input_artifacts = []
        from .inputs import INPUT_PATHS
        from ..config import resolve_path
        for field in INPUT_PATHS:
            section, key = field.split(".")
            value = reader.config.get(section, {}).get(key)
            if not value:
                continue
            # Config inputs follow the CLI's PROJECT_ROOT semantics, not
            # resolve_run_artifact's rebasing of generated stage outputs.
            source = resolve_path(reader.config, value)
            source = Path(os.path.abspath(source))
            input_root = self._input_root(source)
            _checked_directory(source.parent)
            relative = str(source.relative_to(input_root))
            content, receipt = _content_receipt(input_root, relative)
            if len(content) != receipt["size"] or _hash(content) != receipt["sha256"]:
                raise RetentionError("입력 파일 내용과 보존 해시가 일치하지 않습니다.")
            stored = f"{ARCHIVE}/inputs/{receipt['sha256']}{source.suffix.lower()}"
            files[stored] = content
            input_artifacts.append({"field": field, "source": str(source), "path": stored,
                                    "sha256": receipt["sha256"], "size": receipt["size"], "status": "retained"})
        execution_inputs, identity_receipts = self._execution_inputs(root, reader.config, input_artifacts)
        record = {"schema_version": SCHEMA_VERSION, "run_id": root.name,
                  "config": copy.deepcopy(reader.config), "state": copy.deepcopy(reader.state),
                  "platform_status": copy.deepcopy(reader.platform_status), "summary": summary,
                  "candidates": candidates, "observations": observations, "structures": structures,
                  "input_sequences": {"config": reader.config.get("target", {}),
                                      "complex": reader.config.get("complex", {}).get("proteins", []),
                                      "fasta_files": sequence_files},
                  "provenance": {"source_files": inventory, "input_artifacts": input_artifacts,
                                 "execution_inputs": execution_inputs,
                                 "input_identity_receipts": identity_receipts,
                                 "retention_policy": "all_final_coordinates_v1"},
                  "capabilities": CAPABILITIES, "pae_status": "removed_by_retention"}
        return _no_arrays(record), files

    def _execution_inputs(self, root, cfg, artifacts):
        """Compare current copies with previously recorded execution inputs.

        Never create or migrate a catalog during preview. A current external
        file is not evidence of the input originally consumed by an older run.
        Auxiliary inputs get small hash receipts without copying model weights.
        """
        from .inputs import INPUT_PATHS
        from ..config import cfg_get, resolve_path
        db = self.workspace / ".nanocofold/result_catalog.sqlite3"
        legacy = {"status": "execution_identity_not_recorded", "original_input_verified": False,
                  "identity_digests": [], "checked_fields": [], "missing_fields": [],
                  "note": "현재 입력 파일의 보관본이며 실행 당시 입력과의 동일성은 확인되지 않았습니다."}
        if not db.exists() and not db.is_symlink():
            return legacy, []
        _checked_directory(db.parent)
        before = _receipt(self.workspace, str(db.relative_to(self.workspace)))
        try:
            with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as connection:
                connection.execute("BEGIN")
                rows = connection.execute(
                    "SELECT result.identity_digest, identity.payload FROM results AS result "
                    "LEFT JOIN identities AS identity ON identity.digest=result.identity_digest "
                    "WHERE json_extract(result.payload,'$.run_root')=? "
                    "AND result.identity_digest IS NOT NULL", (str(root),)).fetchall()
        except sqlite3.Error as exc:
            raise RetentionError("기록된 실행 입력 identity를 읽을 수 없습니다.") from exc
        if _receipt(self.workspace, str(db.relative_to(self.workspace))) != before:
            raise RetentionError("실행 입력 identity를 확인하는 동안 카탈로그가 변경됐습니다.")
        if not rows:
            return legacy, []
        identities = {}
        for digest, payload in rows:
            try:
                identity = json.loads(payload)
                document = identity["document"]
                if (not isinstance(document, dict) or identity.get("digest") != digest or _digest(document) != digest
                        or not isinstance(document.get("inputs"), dict)):
                    raise ValueError("identity content mismatch")
            except (ValueError, TypeError, KeyError) as exc:
                raise RetentionError("기록된 실행 입력 identity가 없거나 손상되었습니다.") from exc
            identities[digest] = document["inputs"]
        copied = {item["field"]: item for item in artifacts}
        verified, missing = {}, set()
        for digest, expected_inputs in identities.items():
            # A newly introduced or missing primary input cannot inherit an
            # older execution identity merely by being present at flush time.
            if set(copied) != set(expected_inputs).intersection(INPUT_PATHS):
                raise RetentionError("현재 입력 파일 목록이 기록된 실행 입력과 다릅니다.")
            for dotted, expected in expected_inputs.items():
                if (not isinstance(expected, dict) or type(expected.get("bytes")) is not int or expected["bytes"] < 0
                        or not isinstance(expected.get("sha256"), str) or len(expected["sha256"]) != 64):
                    raise RetentionError("기록된 실행 입력 해시 형식이 잘못되었습니다.")
                artifact = copied.get(dotted)
                if artifact:
                    actual = artifact
                else:
                    field, separator, auxiliary = dotted.partition(":")
                    value = cfg_get(cfg, field)
                    if not value or separator and auxiliary not in {"residue_mapping.csv", "input_review.json"}:
                        missing.add(dotted)
                        continue
                    source = Path(os.path.abspath(resolve_path(cfg, value)))
                    if separator:
                        source = source.parent / auxiliary
                    try:
                        input_root = self._input_root(source)
                        _checked_directory(source.parent)
                        receipt = _receipt(input_root, str(source.relative_to(input_root)))
                    except (OSError, RetentionError):
                        missing.add(dotted)
                        continue
                    actual = {"field": dotted, "source": str(source), "sha256": receipt["sha256"],
                              "size": receipt["size"], "status": "hash_verified_at_retention"}
                if actual["sha256"] != expected["sha256"] or actual["size"] != expected["bytes"]:
                    raise RetentionError("현재 입력 파일이 기록된 실행 당시 입력과 다릅니다. 원본을 먼저 복구하세요.")
                if dotted not in verified:
                    verified[dotted] = {**actual, "identity_digests": []}
                verified[dotted]["identity_digests"].append(digest)
        proof = {"status": "matches_recorded_execution_identity" if not missing else "primary_inputs_match_recorded_execution_identity",
                 "original_input_verified": not missing, "identity_digests": sorted(identities),
                 "checked_fields": sorted(verified), "missing_fields": sorted(missing)}
        for receipt in verified.values():
            receipt["identity_digests"].sort()
        return proof, [verified[key] for key in sorted(verified)]

    def _input_root(self, source):
        from ..config import PROJECT_ROOT
        for allowed in (self.workspace, Path(os.path.abspath(PROJECT_ROOT))):
            if source.is_relative_to(allowed):
                _checked_directory(allowed)
                return allowed
        raise RetentionError("입력 파일은 소유 작업 공간 또는 CLI 프로젝트 안에 있어야 합니다. 원본 입력을 먼저 보존하세요.")

    def _verify_inputs(self, artifacts):
        for artifact in artifacts:
            if artifact.get("status") not in {"retained", "hash_verified_at_retention"}:
                continue
            source = Path(artifact["source"])
            if not source.is_absolute():
                raise RetentionError("검토한 입력 파일은 절대 경로여야 합니다.")
            input_root = self._input_root(source)
            _checked_directory(source.parent)
            current = _receipt(input_root, str(source.relative_to(input_root)))
            if current["size"] != artifact["size"] or current["sha256"] != artifact["sha256"]:
                raise RetentionError("검토 후 참조 입력 파일이 변경됐습니다. 다시 검토하세요.")

    def _plan(self, root):
        self._inactive(root)
        inventory = _inventory(root, exclude=(ARCHIVE,))
        existing = retained_record(root)
        if existing:
            original, audit, current = self._reviewed_remaining(root, inventory)
            removed = {entry["path"] for entry in audit["deleted"]}
            remaining = [entry for entry in original["deletable_files"] if entry["path"] not in removed]
            archive_entries = _inventory(root / ARCHIVE)
            archive_bytes = sum(entry["size"] for entry in archive_entries)
            immutable_bytes = sum(entry["size"] for entry in archive_entries
                                  if entry["path"] != "deletion_audit.json")
            # The immutable digest authorizes only the original exact files.
            # A fresh preview measures the remaining subset without changing
            # that authorization or overwriting the original audit intent.
            data = {**original, "current_bytes": sum(entry["size"] for entry in current.values()) + archive_bytes,
                    "minimal_archive_bytes": immutable_bytes,
                    "kept_bytes": original["kept_source_bytes"] + archive_bytes,
                    "deletable_files": remaining,
                    "deletable_bytes": sum(entry["size"] for entry in remaining),
                    "original_deletable_bytes": original["deletable_bytes"],
                    "already_deleted_bytes": sum(entry["size"] for entry in audit["deleted"]),
                    "remaining_deletable_count": len(remaining),
                    "previous_state": audit.get("state"),
                    "retry": bool(remaining),
                    "state": "review_required" if remaining else "flushed",
                    "confirmation_required": bool(remaining)}
            data["estimated_net_reclaimed_bytes"] = data["deletable_bytes"]
            return data, existing, {}
        snapshot, archive_files = self._snapshot(root, inventory)
        # Both the interpreted snapshot and every externally referenced input
        # must still match the exact bytes reviewed, not merely their sizes.
        input_artifacts = snapshot["provenance"]["input_artifacts"] + snapshot["provenance"]["input_identity_receipts"]
        self._verify_inputs(input_artifacts)
        if _inventory(root, exclude=(ARCHIVE,)) != inventory:
            raise RetentionError("보존 내용을 준비하는 동안 원본 결과가 변경됐습니다.")
        # Digest placeholder has exactly the final digest's size. The archive
        # manifest/plan sizes are solved before the review digest.
        snapshot["provenance"]["plan_digest"] = "0" * 64
        archive_files[f"{ARCHIVE}/record.json"] = _encoded(snapshot)
        archive_bytes = sum(map(len, archive_files.values()))
        # In-run configured inputs are dependencies, even when their names
        # resemble disposable stage files. Keep them so a partial deletion
        # never invalidates its own checked input receipts during retry.
        input_sources = {item["source"] for item in input_artifacts}
        kept = [entry for entry in inventory if self._keep(entry) or str(root / entry["path"]) in input_sources]
        deletable = [entry for entry in inventory if not self._keep(entry) and str(root / entry["path"]) not in input_sources]
        current = sum(entry["size"] for entry in inventory)
        data = {"schema_version": SCHEMA_VERSION, "run_id": root.name, "run_root": str(root),
                "current_bytes": current + (archive_bytes if existing else 0),
                "kept_source_bytes": sum(entry["size"] for entry in kept),
                "minimal_archive_bytes": archive_bytes,
                "kept_bytes": sum(entry["size"] for entry in kept) + archive_bytes,
                "deletable_bytes": sum(entry["size"] for entry in deletable),
                "kept_files": kept, "deletable_files": deletable,
                "capabilities": CAPABILITIES, "all_final_models": True,
                "preserved_model_count": len({(item["content_sha256"], item["format"]) for item in snapshot["structures"]}),
                "state": "flushed" if existing and not deletable else "review_required",
                "confirmation_required": True, "deletion_scope": "owned_local_run_only",
                "cloud_or_r2_deletion": False, "input_artifacts": copy.deepcopy(input_artifacts),
                "execution_inputs": copy.deepcopy(snapshot["provenance"]["execution_inputs"]),
                "snapshot_content_sha256": _digest(snapshot),
                "archive_payloads": [{"path": path, "size": len(content), "sha256": _hash(content)}
                                     for path, content in archive_files.items()
                                     if path != f"{ARCHIVE}/record.json"]}
        data["estimated_net_reclaimed_bytes"] = data["deletable_bytes"] - (0 if existing else archive_bytes)
        if not existing:
            for _ in range(10):
                sizing = {**data, "plan_digest": "0" * 64}
                sized_files = {**archive_files, f"{ARCHIVE}/plan.json": _encoded(sizing)}
                manifest = {"schema_version": SCHEMA_VERSION, "files": [
                    {"path": path, "size": len(content), "sha256": "0" * 64}
                    for path, content in sized_files.items()]}
                total = sum(map(len, sized_files.values())) + len(_encoded(manifest))
                if total == data["minimal_archive_bytes"]:
                    break
                data.update(minimal_archive_bytes=total,
                            kept_bytes=data["kept_source_bytes"] + total,
                            estimated_net_reclaimed_bytes=data["deletable_bytes"] - total)
            else:
                raise RetentionError("정리 보관본 크기를 계산할 수 없습니다.")
        data["plan_digest"] = _digest(data)
        return data, snapshot, archive_files

    def _reviewed_remaining(self, root, inventory=None):
        """Validate a durable intent and reconcile its exact remaining files.

        Reconciliation is returned in memory only. plan() must not write an
        audit merely because it previews a previously interrupted deletion.
        """
        plan = _json(root, f"{ARCHIVE}/plan.json")
        digest = plan.get("plan_digest")
        if (plan.get("run_root") != str(root) or not isinstance(digest, str)
                or _digest({key: value for key, value in plan.items() if key != "plan_digest"}) != digest):
            raise RetentionError("보관된 정리 계획의 해시·경로가 일치하지 않습니다.")
        audit_path = root / ARCHIVE / "deletion_audit.json"
        audit = _json(root, f"{ARCHIVE}/deletion_audit.json") if audit_path.exists() else {
            "schema_version": SCHEMA_VERSION, "plan_digest": digest, "deleted": []}
        expected = {entry["path"]: entry for entry in plan["kept_files"] + plan["deletable_files"]}
        deletable = {entry["path"]: entry for entry in plan["deletable_files"]}
        current = {entry["path"]: entry for entry in (inventory if inventory is not None
                                                     else _inventory(root, exclude=(ARCHIVE,)))}
        if audit.get("plan_digest") != digest or not isinstance(audit.get("deleted"), list):
            raise RetentionError("삭제 감사 기록을 확인할 수 없습니다.")
        pending = audit.get("pending")
        rows = audit["deleted"] + ([pending] if pending else [])
        paths = []
        for entry in rows:
            if not isinstance(entry, dict):
                raise RetentionError("삭제 감사 기록 형식이 잘못되었습니다.")
            source = deletable.get(entry.get("path"))
            if not source or any(entry.get(key) != value for key, value in source.items()):
                raise RetentionError("삭제 감사 기록이 검토한 계획과 다릅니다.")
            paths.append(entry["path"])
        if len(paths) != len(set(paths)):
            raise RetentionError("삭제 감사 기록에 중복 파일이 있습니다.")
        # A crash after unlink but before the post-delete receipt is reconciled
        # only from the durable, exact write-ahead intent.
        if pending and pending["path"] not in current:
            audit["deleted"].append({**pending, "deleted_at": time.time(),
                                     "recovered_from_write_ahead": True})
            audit["pending"] = None
        removed = {entry["path"] for entry in audit["deleted"]}
        if set(current) != set(expected) - removed or any(current[path] != expected[path] for path in current):
            raise RetentionError("검토 후 파일이 변경됐습니다. 삭제를 중단합니다.")
        if set(deletable) - removed:
            self._verify_inputs(plan.get("input_artifacts", []))
        return plan, audit, current

    def plan(self, run_root):
        root = self._root(run_root)
        with self._read_lock(root):
            return self._plan(root)[0]

    def _publish(self, root, plan, snapshot, files):
        if _digest(snapshot) != plan["snapshot_content_sha256"]:
            raise RetentionError("검토한 최소 보존 내용이 변경되었습니다.")
        expected_payloads = {entry["path"]: entry for entry in plan["archive_payloads"]}
        payloads = {path: content for path, content in files.items() if path != f"{ARCHIVE}/record.json"}
        if set(payloads) != set(expected_payloads) or any(
                len(content) != expected_payloads[path]["size"]
                or _hash(content) != expected_payloads[path]["sha256"]
                for path, content in payloads.items()):
            raise RetentionError("검토한 구조·입력 보존 파일의 해시가 변경되었습니다.")
        # Staging is an owned sibling of the run, never a shared engine cache.
        with tempfile.TemporaryDirectory(prefix=".retention-stage-", dir=root.parent) as temporary:
            stage = Path(temporary)
            snapshot["provenance"].update(plan_digest=plan["plan_digest"])
            files[f"{ARCHIVE}/record.json"] = _encoded(snapshot)
            files[f"{ARCHIVE}/plan.json"] = _encoded(plan)
            manifest = {"schema_version": SCHEMA_VERSION, "files": []}
            for relative, content in files.items():
                path = stage / Path(relative).relative_to(ARCHIVE)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as handle:
                    handle.write(content); handle.flush(); os.fsync(handle.fileno())
                manifest["files"].append({"path": relative, "size": len(content), "sha256": _hash(content)})
            _atomic_json(stage / "manifest.json", manifest)
            # Validate the complete staged archive before it becomes authoritative.
            for entry in manifest["files"]:
                content = _bytes(stage, str(Path(entry["path"]).relative_to(ARCHIVE)))
                if _hash(content) != entry["sha256"]:
                    raise RetentionError("정리 보관본 쓰기 검증에 실패했습니다.")
            self._verify_inputs(plan["input_artifacts"])
            current = {entry["path"]: entry for entry in _inventory(root, exclude=(ARCHIVE,))}
            expected = {entry["path"]: entry for entry in plan["kept_files"] + plan["deletable_files"]}
            if current != expected:
                raise RetentionError("정리 보관본 쓰기 중 원본 결과가 변경됐습니다.")
            if (root / ARCHIVE).exists() or (root / ARCHIVE).is_symlink():
                raise RetentionError("다른 정리 보관본이 생겼습니다. 다시 검토하세요.")
            os.replace(stage, root / ARCHIVE)
        retained_record(root)

    @staticmethod
    def _unlink(root, entry):
        relative = _safe_relative(entry["path"])
        actual = _receipt(root, relative)
        if actual != entry:
            raise RetentionError("삭제 직전 파일이 변경되었습니다. 정리를 중단합니다.")
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in relative.parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor); descriptor = child
            info = os.stat(relative.name, dir_fd=descriptor, follow_symlinks=False)
            if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (entry["device"], entry["inode"], entry["size"], entry["mtime_ns"]):
                raise RetentionError("삭제 대상 파일 정체성이 변경되었습니다.")
            os.unlink(relative.name, dir_fd=descriptor)
        finally:
            os.close(descriptor)

    def apply(self, run_root, plan_digest, confirmation):
        if confirmation is not True:
            raise RetentionError("검토한 파일 삭제에 대한 명시적 확인이 필요합니다.")
        root = self._root(run_root)
        from ..state import RunLock, RunLockedError
        try:
            with RunLock(root):
                self._inactive(root)
                record = retained_record(root)
                if record:
                    plan, audit, _ = self._reviewed_remaining(root)
                    if plan["plan_digest"] != plan_digest:
                        raise RetentionError("검토한 정리 계획과 다릅니다. 다시 확인하세요.")
                    audit_path = root / ARCHIVE / "deletion_audit.json"
                    removed = {entry["path"] for entry in audit["deleted"]}
                    if audit.get("state") == "flushed" and removed == {entry["path"] for entry in plan["deletable_files"]}:
                        return audit
                else:
                    plan, snapshot, files = self._plan(root)
                    if plan["plan_digest"] != plan_digest:
                        raise RetentionError("검토 후 파일이 변경됐습니다. 다시 검토하세요.")
                    self._publish(root, plan, snapshot, files)
                    audit = {"schema_version": SCHEMA_VERSION, "plan_digest": plan_digest, "deleted": []}
                    audit_path = root / ARCHIVE / "deletion_audit.json"
                audit.update(state="deleting", error=None, updated=time.time())
                _atomic_json(audit_path, audit)
                removed = {entry["path"] for entry in audit["deleted"]}
                for entry in plan["deletable_files"]:
                    if entry["path"] in removed:
                        continue
                    try:
                        audit.update(pending=entry, updated=time.time())
                        _atomic_json(audit_path, audit)
                        self._verify_inputs(plan.get("input_artifacts", []))
                        self._unlink(root, entry)
                        audit["deleted"].append({**entry, "deleted_at": time.time()})
                        audit.update(pending=None, updated=time.time())
                        _atomic_json(audit_path, audit)
                    except (OSError, ValueError) as exc:
                        audit.update(state="partial_failure", error=f"{type(exc).__name__}: {exc}", updated=time.time())
                        _atomic_json(audit_path, audit)
                        return audit
                audit.update(state="flushed", updated=time.time(), deleted_bytes=sum(entry["size"] for entry in audit["deleted"]))
                _atomic_json(audit_path, audit)
                return audit
        except RunLockedError as exc:
            raise RetentionError("현재 실행 중인 작업은 정리할 수 없습니다.") from exc
