"""Durable queue and detached CLI workers. GPU slots and run locks span servers."""
from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import yaml

from ..install import PROFILES as INSTALL_PROFILES
from .inputs import INPUT_PATHS, normalized_config
from .paths import contained, sha256

TERMINAL = {"cancelled", "interrupted", "failed", "partial_failure", "budget_exhausted", "complete"}
ACTIVE = {"queued", "running", "cancelling"}
PROFILES = set(INSTALL_PROFILES)


def gpu_identifier(value) -> str:
    value = str(value)
    if re.fullmatch(r"[0-9]+", value):
        return str(int(value))
    if re.fullmatch(r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value):
        return value.lower().replace("gpu-", "GPU-", 1)
    raise ValueError("GPU는 0 이상의 번호 또는 전체 GPU UUID여야 합니다.")


def allowed_gpu(value) -> str:
    gpu = gpu_identifier(value)
    if "NANOCOFOLD_ALLOWED_GPUS" in os.environ:
        raw = os.environ["NANOCOFOLD_ALLOWED_GPUS"].split(",")
        if any(not item.strip() for item in raw):
            raise ValueError("서버의 허용 GPU 설정이 비어 있거나 손상되었습니다.")
        permitted = {gpu_identifier(item.strip()) for item in raw}
        if gpu not in permitted:
            raise ValueError("이 GPU는 현재 계산 서버에서 허용되지 않았습니다.")
    return gpu


def gpu_lock_path(gpu: str) -> Path:
    # A persistent inode coordinates every workspace for this Unix user.
    # Never unlink this file: a replaced inode could permit concurrent holders.
    directory = os.environ.get("NANOCOFOLD_GPU_LOCK_DIR")
    root = Path(directory) if directory else Path.home() / ".cache" / "nanocofold"
    if not root.is_absolute():
        raise ValueError("서버 GPU 잠금 경로는 절대 경로여야 합니다.")
    return root / f"gpu-{gpu_identifier(gpu)}.lock"


def msa_acquisition_status(run_dir) -> dict:
    path = Path(run_dir) / "01_msa/public_acquisition.json"
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text())
        stats = document["stats"]
        result = {}
        for key in ("cache_hits", "unique_missing", "post_requests", "poll_requests", "download_requests"):
            value = stats[key]
            if type(value) is not int or value < 0:
                raise ValueError("MSA 요청 수 형식 오류")
            result[key] = value
        for key in ("source", "mode"):
            if isinstance(stats.get(key), str):
                result[key] = stats[key][:80]
        return {"msa_acquisition": {"stats": result}}
    except (OSError, ValueError, KeyError, TypeError):
        return {"msa_acquisition": {"error": "MSA 요청 기록을 읽지 못했습니다."}}


def write_platform_status(job: dict):
    """Persist portable terminal provenance beside this platform job's artifacts."""
    if job.get("reused_result_id") or job.get("kind") != "analysis" or job.get("state") not in TERMINAL or not job.get("run_dir"):
        return
    root = Path(job["run_dir"])
    if not root.is_dir():
        return
    payload = {"schema_version": 1, "job_id": job["id"], "state": job["state"],
               "updated": job.get("updated", time.time()), "created": job.get("created"),
               "started": job.get("started"), "finished": job.get("finished"),
               "elapsed_seconds": job.get("elapsed_seconds", 0), "budget_seconds": job.get("budget_seconds"),
               "history": job.get("history", []), "error": job.get("error"),
               "mode": job.get("mode"), "mock": job.get("mock"), "source": "platform_job"}
    path = root / "platform_status.json"
    temporary = root / (".platform_status-" + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def process_stamp(pid: int | None) -> str | None:
    if not pid:
        return None
    try:
        path = Path(f"/proc/{pid}/stat")
        if path.exists():
            fields = path.read_text().rsplit(")", 1)[1].split()
            if fields[0] == "Z":
                return None
            return fields[19]  # Linux starttime, insensitive to pid reuse.
        result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True, timeout=2)
        return result.stdout.strip() or None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def descendants(pid: int) -> list[tuple[int, str | None]]:
    """Snapshot descendants before SIGTERM, including tools in new process groups."""
    try:
        result = subprocess.run(["ps", "-eo", "pid=,ppid="], capture_output=True, text=True, timeout=3)
        pairs = [tuple(map(int, line.split())) for line in result.stdout.splitlines() if len(line.split()) == 2]
        found = {pid}
        while True:
            added = {child for child, parent in pairs if parent in found} - found
            if not added:
                break
            found.update(added)
        return [(child, process_stamp(child)) for child in found]
    except (ValueError, OSError, subprocess.SubprocessError):
        return [(pid, process_stamp(pid))]


class JobStore:
    def __init__(self, workspace: Path):
        from .eta import ComplexJobReadCache
        self._complex_read_cache = ComplexJobReadCache()
        self.workspace = Path(workspace).resolve()
        self.root = self.workspace / ".nanocofold"
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "jobs.sqlite3"
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL, payload TEXT NOT NULL)""")
            connection.execute("CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state,created)")
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
            if "needs_cleanup" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN needs_cleanup INTEGER NOT NULL DEFAULT 0")
                connection.execute("UPDATE jobs SET needs_cleanup=1 WHERE json_array_length(json_extract(payload,'$.descendants'))>0")
            connection.execute("CREATE INDEX IF NOT EXISTS jobs_cleanup ON jobs(needs_cleanup) WHERE needs_cleanup=1")
            connection.execute("CREATE INDEX IF NOT EXISTS jobs_history ON jobs(created DESC,id DESC)")
            connection.execute("CREATE INDEX IF NOT EXISTS jobs_identity_active ON jobs(json_extract(payload,'$.execution_identity.digest')) WHERE state IN ('queued','running','cancelling')")
            connection.execute("CREATE INDEX IF NOT EXISTS jobs_run_dir ON jobs(json_extract(payload,'$.run_dir'))")
            connection.execute("CREATE INDEX IF NOT EXISTS jobs_active_run_dir ON jobs(json_extract(payload,'$.run_dir')) WHERE state IN ('queued','running','cancelling')")
            connection.execute("CREATE TABLE IF NOT EXISTS submission_links (id TEXT PRIMARY KEY,submission_hash TEXT NOT NULL,job_id TEXT NOT NULL)")

    @contextlib.contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def count(self) -> int:
        """Heartbeat statistics must not parse/reconcile every result directory."""
        with self.connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    def list(self, *, states=None, kind=None, limit=None, before=None, before_id=None, cleanup=False) -> list[dict]:
        clauses, values = [], []
        if kind is not None:
            clauses.append("json_extract(payload, '$.kind')=?")
            values.append(kind)
        if states is not None:
            if not states:
                return []
            clauses.append("state IN (" + ",".join("?" for _ in states) + ")")
            values.extend(sorted(states))
        if cleanup:
            clauses.append("needs_cleanup=1")
        if before is not None:
            if before_id is not None:
                clauses.append("(created<? OR (created=? AND id<?))")
                values.extend((before, before, before_id))
            else:
                clauses.append("created<?")
                values.append(before)
        query = "SELECT payload FROM jobs" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY created DESC,id DESC"
        if limit is not None:
            if type(limit) is not int or not 1 <= limit <= 1000:
                raise ValueError("작업 페이지 크기는 1–1,000이어야 합니다.")
            query += " LIMIT ?"
            values.append(limit)
        with self.connect() as connection:
            return [self._read_payload(row["payload"]) for row in connection.execute(query, values)]

    def _read_payload(self, payload):
        from .errors import public_error_data
        return public_error_data(self._complex_read_cache.read(json.loads(payload), record_key=payload, terminal_states=TERMINAL))

    def get(self, identifier: str) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT payload FROM jobs WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError("작업이 없습니다.")
        return self._read_payload(row["payload"])

    def submitted(self, identifier: str):
        with self.connect() as connection:
            row = connection.execute("SELECT submission_hash,job_id FROM submission_links WHERE id=?", (identifier,)).fetchone()
        if row:
            return row["submission_hash"], self.get(row["job_id"])
        return None

    def update(self, identifier: str, **changes) -> dict:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM jobs WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError(identifier)
            job = json.loads(row["payload"])
            job.update(changes, updated=time.time())
            connection.execute("UPDATE jobs SET state=?,updated=?,payload=?,needs_cleanup=? WHERE id=?",
                               (job["state"], job["updated"], json.dumps(job), int(bool(job.get("descendants"))), identifier))
        return job

    def insert(self, job: dict):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            identity = job.get("execution_identity", {})
            if job.get("reused_result_id"):
                current_source = connection.execute("SELECT payload FROM jobs WHERE state IN ('queued','running','cancelling') AND json_extract(payload,'$.run_dir')=? LIMIT 1", (job["run_dir"],)).fetchone()
                if current_source:
                    source = json.loads(current_source["payload"])
                    current_identity = source.get("execution_identity", {})
                    if (source.get("kind") != "analysis" or not identity.get("digest")
                            or current_identity.get("digest") != identity["digest"]
                            or current_identity.get("reusable") is not True):
                        raise ValueError("원본 결과가 다른 조건으로 재개되었습니다. 결과를 다시 확인한 후 제출하세요.")
                    if job.get("submission_hash"):
                        connection.execute("INSERT INTO submission_links VALUES(?,?,?)", (job["id"], job["submission_hash"], source["id"]))
                    return source
            if job["kind"] == "analysis" and job["state"] in ACTIVE and job.get("reuse_results") and identity.get("reusable"):
                same = connection.execute("SELECT payload FROM jobs WHERE state IN ('queued','running','cancelling') AND kind='analysis' AND json_extract(payload,'$.execution_identity.digest')=? LIMIT 1",
                                          (identity["digest"],)).fetchone()
                if same:
                    # Serialize simultaneous exact requests inside SQLite;
                    # callers follow the original queued/running job ID.
                    source = json.loads(same["payload"])
                    if job.get("submission_hash"):
                        connection.execute("INSERT INTO submission_links VALUES(?,?,?)", (job["id"], job["submission_hash"], source["id"]))
                    return source
            if job["kind"] == "setup":
                conflict = connection.execute("SELECT 1 FROM jobs WHERE kind='setup' AND state IN ('queued','running','cancelling')").fetchone()
            else:
                conflict = connection.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running','cancelling') AND json_extract(payload,'$.run_dir')=?",
                                              (job.get("run_dir"),)).fetchone() if job.get("run_dir") else None
            if conflict:
                raise ValueError("같은 작업 공간의 설치 또는 분석이 이미 실행·대기 중입니다.")
            connection.execute("INSERT INTO jobs(id,kind,state,created,updated,payload) VALUES(?,?,?,?,?,?)",
                               (job["id"], job["kind"], job["state"], job["created"], job["updated"], json.dumps(job)))
            if job.get("submission_hash"):
                connection.execute("INSERT INTO submission_links VALUES(?,?,?)", (job["id"], job["submission_hash"], job["id"]))
        return job


class JobManager:
    def __init__(self, workspace: Path, *, interval: float = .3):
        self.workspace = Path(workspace).resolve()
        self.store = JobStore(self.workspace)
        from .result_catalog import ResultCatalog
        self.catalog = ResultCatalog(self.workspace)
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = None
        self.children: dict[str, subprocess.Popen] = {}
        self.failed_launches: dict[str, subprocess.Popen] = {}
        self.pending_kills: dict[str, tuple[float, list[tuple[int, str | None]]]] = {}
        self.observed_descendants: dict[str, list[tuple[int, str | None]]] = {}
        self.lockfile = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, name="nanocofold-queue", daemon=True)
        self.thread.start()

    def close(self):
        # Workers deliberately survive an HTTP server shutdown.
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def create(self, payload: dict, *, kind: str = "analysis") -> dict:
        from .maintenance import maintenance_lock
        with maintenance_lock(self.workspace):
            return self._submit(payload, kind=kind)

    def _submit(self, payload: dict, *, kind: str = "analysis") -> dict:
        submission = payload.get("submission_id")
        if not submission:
            return self._create(payload, kind=kind)
        if kind != "analysis" or not isinstance(submission, str) or not re.fullmatch(r"[A-Za-z0-9_-]{12,100}", submission):
            raise ValueError("잘못된 제출 ID입니다.")
        import fcntl, hashlib
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        identifier = uuid.uuid5(uuid.NAMESPACE_URL, "nanocofold-sites:" + submission).hex
        path = self.store.root / ("submission-" + identifier + ".lock")
        with path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            linked = self.store.submitted(identifier)
            if linked:
                if linked[0] != digest:
                    raise ValueError("같은 제출 ID의 입력 또는 조건이 변경되었습니다.")
                return linked[1]
            try:
                existing = self.store.get(identifier)
            except KeyError:
                existing = None
            if existing:
                if existing.get("submission_hash") != digest:
                    raise ValueError("같은 제출 ID의 입력 또는 조건이 변경되었습니다.")
                return existing
            return self._create(payload, kind=kind, identifier=identifier, submission_hash=digest)

    def _create(self, payload: dict, *, kind: str = "analysis", identifier=None, submission_hash=None) -> dict:
        identifier = identifier or uuid.uuid4().hex
        folder = self.store.root / "jobs" / identifier
        override = payload.get("config")
        if kind == "diagnostics" and payload.get("environment_only") is True:
            from .environment import normalized_installation_config
            cfg = normalized_installation_config(self.workspace, override, mode=payload.get("mode"))
        else:
            cfg = normalized_config(self.workspace, override)
        if payload.get("mode"):
            if payload["mode"] not in {"complex", "design", "screen", "mature"}:
                raise ValueError("지원하지 않는 모드입니다.")
            cfg["run"]["mode"] = payload["mode"]
        if "mock" in payload:
            if not isinstance(payload["mock"], bool):
                raise ValueError("mock는 true/false여야 합니다.")
            cfg["run"]["mock"] = bool(payload["mock"])
        if payload.get("name"):
            cfg["run"]["name"] = str(payload["name"])[:120]
        if cfg["run"].get("dry_run"):
            raise ValueError("플랫폼 실행은 실제 분석 또는 mock 데모를 사용하세요.")
        if kind == "analysis":
            if cfg["run"]["mode"] == "complex":
                from ..complex import proteins, validate_settings
                proteins(cfg.get("complex", {}).get("proteins"))
                validate_settings(cfg.get("complex", {}))
            target = cfg.get("target", {})
            if not (target.get("sequence") or target.get("fasta") or target.get("pdb")):
                raise ValueError("검토한 표적 구조 또는 FASTA가 필요합니다.")
            if cfg["run"]["mode"] == "screen" and not cfg["run"].get("mock") and not cfg["screen"].get("vhh_fasta"):
                raise ValueError("VHH 라이브러리 FASTA가 필요합니다.")
            if cfg["run"]["mode"] == "mature" and not (cfg["mature"].get("wt_sequence") or cfg["mature"].get("wt_fasta")):
                raise ValueError("WT VHH 서열이 필요합니다.")
        gpu = allowed_gpu(payload.get("gpu", os.environ.get("NANOCOFOLD_GPU", "0")))
        budget = payload.get("budget_seconds")
        if budget is not None and (isinstance(budget, bool) or not isinstance(budget, (int, float)) or not 0 < budget <= 604800):
            raise ValueError("실행 시간 한도는 0초 초과, 7일 이하여야 합니다.")
        if "reuse_results" in payload and type(payload["reuse_results"]) is not bool:
            raise ValueError("reuse_results는 true/false여야 합니다.")
        run_dir = self.workspace / "runs" / identifier if kind == "analysis" else None
        folder.mkdir(parents=True, exist_ok=True)
        manifest = []
        # Store independent input snapshots; never read a changed upload midway through inference.
        for dotted in INPUT_PATHS:
            section, key = dotted.split(".")
            if cfg.get(section, {}).get(key):
                import shutil
                original = contained(self.workspace, cfg[section][key], exists=True)
                snapshot = folder / (dotted.replace(".", "_") + original.suffix)
                shutil.copy2(original, snapshot)
                cfg[section][key] = str(snapshot)
                manifest.append({"field": dotted, "source": str(original), "snapshot": snapshot.name, "sha256": sha256(snapshot)})
                if dotted in {"target.pdb", "input_review.reference_path"}:
                    for auxiliary in ("residue_mapping.csv", "input_review.json"):
                        source = original.parent / auxiliary
                        if source.is_file():
                            source = contained(self.workspace, source)
                            shutil.copy2(source, folder / auxiliary)
                            manifest.append({"field": auxiliary, "source": str(source), "snapshot": auxiliary, "sha256": sha256(folder / auxiliary)})
        config_path = folder / "config.yaml"
        config_path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
        provenance = {"schema_version": 1, "inputs": manifest, "config_sha256": sha256(config_path),
                      "python": sys.version, "mode": cfg["run"]["mode"], "mock": bool(cfg["run"].get("mock"))}
        (folder / "manifest.json").write_text(json.dumps(provenance, indent=2))
        now = time.time()
        job = {"id": identifier, "kind": kind, "state": "queued", "created": now, "updated": now,
               "name": cfg["run"]["name"] if kind == "analysis" else kind,
               "mode": cfg["run"]["mode"], "mock": bool(cfg["run"].get("mock")), "gpu": gpu,
               "run_dir": str(run_dir) if run_dir else None, "config_path": str(config_path),
               "log_path": str(folder / "worker.log"), "result_id": None,
               "budget_seconds": budget, "elapsed_seconds": 0, "phase": "queued", "progress": {"completed": 0, "total": None},
               "pid": None, "process_stamp": None, "pgid": None, "started": None, "finished": None,
               "error": None, "can_resume": False, "history": [], "resume": False,
               "profile": payload.get("profile"), "submission_id": payload.get("submission_id"), "submission_hash": submission_hash}
        if kind == "diagnostics" and payload.get("environment_only") is True:
            job["environment_only"] = True
            from .environment import environment_scope
            job["environment_scope"] = environment_scope(self.workspace, cfg, gpu=gpu)
        if kind == "analysis":
            from .result_catalog import build_identity
            identity = build_identity(self.workspace, cfg, gpu)
            self.catalog.store_identity(identity)
            job["execution_identity"] = {key: identity[key] for key in ("digest", "reusable", "reasons")}
            job["reuse_results"] = payload.get("reuse_results", True)
            if job["reuse_results"] and identity["reusable"]:
                with self.catalog.reuse_lease(identity["digest"]) as source:
                    if source:
                        job.update(state="complete", phase="reused", finished=now, result_id=source["id"],
                                   run_dir=source["run_root"], reused_result_id=source["id"],
                                   reuse_kind=source["reuse_kind"], full_execution_available=source["full_execution_available"],
                                   reused=True, can_resume=False, source_elapsed_seconds=source.get("elapsed_seconds"))
                        # Keep source ownership through the alias transaction.
                        # Canonical artifacts/status are never rewritten.
                        return self.store.insert(job)
        if kind == "setup" and job["profile"] not in PROFILES:
            raise ValueError("설치 프로필은 " + "/".join(sorted(PROFILES)) + " 중 하나입니다.")
        return self.store.insert(job)

    def resume(self, identifier: str, additional_budget_seconds=None) -> dict:
        from .maintenance import maintenance_lock
        with maintenance_lock(self.workspace):
            return self._resume(identifier, additional_budget_seconds)

    def _resume(self, identifier: str, additional_budget_seconds=None) -> dict:
        job = self.store.get(identifier)
        if job.get("run_dir") and (Path(job["run_dir"]) / "retained").exists():
            raise ValueError("정리 보관본은 재개할 수 없습니다. 원본 계산이 필요하면 새 분석으로 재실행하세요.")
        allowed_gpu(job["gpu"])
        if job["kind"] != "analysis" or job["state"] not in {"interrupted", "failed", "partial_failure", "budget_exhausted", "cancelled"}:
            raise ValueError("이 작업은 재개할 수 없습니다.")
        if identifier in self.pending_kills or (job.get("process_stamp") and process_stamp(job.get("pid")) == job["process_stamp"]):
            raise ValueError("앞선 실행 프로세스를 정리 중입니다. 잠시 뒤 재개하세요.")
        if additional_budget_seconds is not None and (isinstance(additional_budget_seconds, bool)
                or not isinstance(additional_budget_seconds, (int, float)) or not 0 < additional_budget_seconds <= 604800):
            raise ValueError("추가 실행 시간은 0초 초과, 7일 이하여야 합니다.")
        if job["state"] == "budget_exhausted" and additional_budget_seconds is None:
            raise ValueError("시간 한도가 소진되었습니다. 재개할 추가 시간(분)을 입력하세요.")
        manifest = json.loads((Path(job["config_path"]).parent / "manifest.json").read_text())
        if sha256(Path(job["config_path"])) != manifest["config_sha256"]:
            raise ValueError("저장된 실행 설정이 바뀌었습니다. 새 작업을 생성하세요.")
        for entry in manifest["inputs"]:
            path = Path(job["config_path"]).parent / entry["snapshot"]
            if not path.is_file() or sha256(path) != entry["sha256"]:
                raise ValueError("입력 스냅샷이 없거나 변경되었습니다. 새 작업을 생성하세요.")
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM jobs WHERE id=?", (identifier,)).fetchone()
            current = json.loads(row["payload"])
            if current["state"] not in {"interrupted", "failed", "partial_failure", "budget_exhausted", "cancelled"}:
                raise ValueError("작업이 이미 대기 또는 실행 중입니다.")
            if current["state"] == "budget_exhausted" and additional_budget_seconds is None:
                raise ValueError("시간 한도가 소진되었습니다. 재개할 추가 시간(분)을 입력하세요.")
            previous = {"state": current["state"], "finished": current.get("finished"), "elapsed_seconds": current.get("elapsed_seconds", 0)}
            if additional_budget_seconds is not None:
                previous["budget_extension"] = {"previous_budget_seconds": current.get("budget_seconds"),
                                                 "additional_budget_seconds": additional_budget_seconds}
                current["budget_seconds"] = current.get("elapsed_seconds", 0) + additional_budget_seconds
                previous["budget_extension"]["budget_seconds"] = current["budget_seconds"]
            history = current.get("history", []) + [previous]
            current.update(state="queued", resume=Path(current["run_dir"]).exists(), started=None, finished=None,
                           pid=None, pgid=None, process_stamp=None, error=None, history=history, can_resume=False,
                           budget_exceeded=False, updated=time.time())
            current.update(phase="resume_validation", progress={"completed": 0, "total": current.get("progress", {}).get("total")})
            connection.execute("UPDATE jobs SET state='queued',updated=?,payload=? WHERE id=?", (current["updated"], json.dumps(current), identifier))
        return current

    def cancel(self, identifier: str) -> dict:
        # Dispatch and worker completion can happen between an API read and
        # write. Decide the transition from the current row under the same
        # transaction that persists it, keeping a live worker cancellable.
        self.store.get(identifier)  # Preserve the public missing-job error.
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM jobs WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError("작업이 없습니다.")
            job = json.loads(row["payload"])
            if job["state"] == "queued":
                job.update(state="cancelled", phase="cancelled", finished=time.time(),
                           can_resume=job["kind"] == "analysis")
            elif job["state"] in {"running", "cancelling"}:
                job.update(state="cancelling", phase="cancelling")
            else:
                return job
            job["updated"] = time.time()
            connection.execute("UPDATE jobs SET state=?,updated=?,payload=? WHERE id=?",
                               (job["state"], job["updated"], json.dumps(job), identifier))
        write_platform_status(job)
        return job

    def logs(self, identifier: str, offset: int = 0) -> dict:
        job = self.store.get(identifier)
        path = Path(job["log_path"])
        offset = max(0, offset)
        if path.exists():
            with path.open("rb") as handle:
                size = path.stat().st_size
                offset = min(offset, size)
                handle.seek(offset)
                data = handle.read(256 * 1024)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                if len(data) == 256 * 1024 and exc.end == len(data) and exc.start >= len(data) - 4:
                    data = data[:exc.start]  # Finish this character on the next polling request.
                text = data.decode("utf-8", errors="replace")
        else:
            data, text = b"", ""
        return {"offset": offset, "next_offset": offset + len(data), "text": text, "done": job["state"] in TERMINAL}

    def _spawn(self, job: dict) -> bool:
        import fcntl
        try:
            allowed_gpu(job["gpu"])
        except ValueError as exc:
            self.store.update(job["id"], state="failed", phase="gpu_policy", error=str(exc),
                              finished=time.time(), can_resume=False)
            return False
        path = gpu_lock_path(job["gpu"])
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.open("a+")
        try:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if job.get("phase") != "waiting_for_gpu":
                    self.store.update(job["id"], phase="waiting_for_gpu", waiting_for="gpu")
                return False
            identity = os.fstat(lock.fileno())
            with self.store.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT payload FROM jobs WHERE id=?", (job["id"],)).fetchone()
                current = json.loads(row["payload"])
                if current["state"] != "queued":
                    return False
                current.update(state="running", phase="starting", started=time.time(), finished=None, updated=time.time(),
                               waiting_for=None, gpu_lock={"path": str(path), "device": identity.st_dev,
                                                         "inode": identity.st_ino, "scope": "user_host"})
                connection.execute("UPDATE jobs SET state='running',updated=?,payload=? WHERE id=?", (current["updated"], json.dumps(current), job["id"]))
                job = current
            environment = os.environ.copy()
            package_root = str(Path(__file__).resolve().parents[2])
            # Bootstrap discovery belongs only to the core worker interpreter.
            # A wheel's package_root is its Python-version-specific site-packages;
            # adding it to PYTHONPATH would also contaminate engine subprocesses
            # that intentionally use another Python ABI and dependency set.
            bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module(sys.argv.pop(1),run_name='__main__',alter_sys=True)"
            environment["NANOCOFOLD_ROOT"] = str(self.workspace)
            environment["PYTHONUNBUFFERED"] = "1"
            environment["CUDA_VISIBLE_DEVICES"] = job["gpu"]
            environment["NANOCOFOLD_GPU_LOCK_FD"] = str(lock.fileno())
            with Path(job["log_path"]).open("ab", buffering=0) as log:
                child = subprocess.Popen([sys.executable, "-u", "-c", bootstrap, package_root, "nanocofold.platform.worker", "--workspace", str(self.workspace), "--job", job["id"]],
                                         cwd=self.workspace, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                         start_new_session=True, pass_fds=(lock.fileno(),))
            self.children[job["id"]] = child
            try:
                stamp = process_stamp(child.pid)
                if not stamp:
                    raise RuntimeError("작업 등록에 필요한 프로세스 시작 식별자를 확인하지 못했습니다.")
                self.store.update(job["id"], pid=child.pid, pgid=child.pid, process_stamp=stamp)
            except BaseException:
                # The DB write can fail before or after its commit. In either
                # case the Popen handle still owns this child: stop and reap it
                # before allowing reconciliation to call the launch interrupted.
                self.failed_launches[job["id"]] = child
                self._reap_failed_launch(job["id"], child)
                raise
            return True
        finally:
            # The inherited descriptor shares this flock's open-file description.
            # LOCK_UN here would release the live worker's lock too. Closing only
            # the parent's descriptor retains it until the detached worker exits.
            lock.close()

    def _reap_failed_launch(self, identifier: str, child: subprocess.Popen):
        targets = list(self.observed_descendants.get(identifier, []))
        if child.poll() is None:
            targets = list(dict.fromkeys(targets + descendants(child.pid)))
            self.observed_descendants[identifier] = targets
            with contextlib.suppress(ProcessLookupError):
                child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                child.kill()
            child.wait(timeout=3)
        # A write may have committed before raising, allowing the registered
        # worker to begin. Retain and verify independent-session descendants as
        # well; numeric PIDs alone never authorize signalling another process.
        for pid, stamp in targets:
            if pid != child.pid and stamp and process_stamp(pid) == stamp:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
        self.observed_descendants.pop(identifier, None)
        self.children.pop(identifier, None)
        self.failed_launches.pop(identifier, None)

    def _terminate(self, job: dict):
        identifier = job["id"]
        if identifier not in self.pending_kills:
            targets = descendants(job["pid"])
            self.pending_kills[identifier] = (time.monotonic(), targets)
            if process_stamp(job["pid"]) == job.get("process_stamp"):
                with contextlib.suppress(ProcessLookupError):
                    os.kill(job["pid"], signal.SIGTERM)
        started, targets = self.pending_kills[identifier]
        if time.monotonic() - started < 3:
            return
        for pid, stamp in reversed(targets):
            if stamp and process_stamp(pid) == stamp:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
        self.pending_kills.pop(identifier, None)
        latest = self.store.get(identifier)
        state = "budget_exhausted" if latest.get("budget_exceeded") else "cancelled"
        now = time.time()
        previous_elapsed = latest.get("history", [])[-1].get("elapsed_seconds", 0) if latest.get("history") else 0
        elapsed = previous_elapsed + now - (latest.get("started") or now)
        terminal = self.store.update(identifier, state=state, phase=state, finished=now, elapsed_seconds=elapsed,
                                     can_resume=job["kind"] == "analysis")
        write_platform_status(terminal)

    def _tick(self):
        # A failed cleanup must retain the known Popen owner, even when the
        # registration write never produced a durable PID/birth stamp.
        for identifier, child in list(self.failed_launches.items()):
            self._reap_failed_launch(identifier, child)
        for identifier in list(self.pending_kills):
            self._terminate(self.store.get(identifier))
        jobs_by_id = {job["id"]: job for job in self.store.list(states=ACTIVE)}
        for job in self.store.list(states=TERMINAL, cleanup=True):
            jobs_by_id[job["id"]] = job
        for identifier in set(self.children) | set(self.observed_descendants):
            if identifier not in jobs_by_id:
                with contextlib.suppress(KeyError):
                    jobs_by_id[identifier] = self.store.get(identifier)
        jobs = list(jobs_by_id.values())
        for job in jobs:
            if job["state"] in TERMINAL:
                child = self.children.get(job["id"])
                if child and child.poll() is not None:
                    self.children.pop(job["id"], None)
                recorded = self.observed_descendants.pop(job["id"], job.get("descendants", []))
                for pid, stamp in recorded:
                    if pid != job.get("pid") and stamp and process_stamp(pid) == stamp:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(os.getpgid(pid), signal.SIGKILL)
                if recorded:
                    self.store.update(job["id"], descendants=[], last_descendants=recorded)
                continue
            if job["state"] not in {"running", "cancelling"}:
                continue
            identifier = job["id"]
            child = self.children.get(identifier)
            alive = bool(job.get("process_stamp")) and process_stamp(job.get("pid")) == job["process_stamp"]
            if child:
                child.poll()  # reap our child; worker itself persists successful completion.
            if not alive:
                current = self.store.get(identifier)
                # Tool processes can use independent sessions. Reap descendants
                # recorded before a forced worker death, even after reparenting.
                for pid, stamp in self.observed_descendants.pop(identifier, job.get("descendants", [])):
                    if stamp and process_stamp(pid) == stamp:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(os.getpgid(pid), signal.SIGKILL)
                if current["state"] in {"running", "cancelling"}:
                    state = "cancelled" if current["state"] == "cancelling" else "interrupted"
                    if current.get("budget_exceeded"):
                        state = "budget_exhausted"
                    now = time.time()
                    previous_elapsed = current.get("history", [])[-1].get("elapsed_seconds", 0) if current.get("history") else 0
                    elapsed = previous_elapsed + now - (current.get("started") or now)
                    terminal = self.store.update(identifier, state=state, phase=state, finished=now, elapsed_seconds=elapsed,
                                                 error="실행 프로세스가 종료되었습니다. 기록된 완료 단계만 재개할 수 있습니다.", can_resume=job["kind"] == "analysis")
                    write_platform_status(terminal)
                # A dead worker may leave tool children; finish pending cleanup.
                if identifier in self.pending_kills:
                    self._terminate(job)
                self.children.pop(identifier, None)
                continue
            previous_elapsed = job.get("history", [])[-1].get("elapsed_seconds", 0) if job.get("history") else 0
            elapsed = previous_elapsed + time.time() - (job["started"] or time.time())
            observed = descendants(job["pid"])
            self.observed_descendants[identifier] = observed
            if job.get("budget_seconds") and elapsed >= job["budget_seconds"]:
                job = self.store.update(identifier, state="cancelling", phase="budget_exhausted", budget_exceeded=True)
            if job["state"] == "cancelling":
                self._terminate(job)
            else:
                progress = {**self._progress(job), **msa_acquisition_status(job["run_dir"])} if job.get("run_dir") else self._progress(job)
                self.store.update(identifier, elapsed_seconds=elapsed, descendants=observed, **progress)
        jobs = self.store.list(states=ACTIVE)
        occupied = {job["gpu"] for job in jobs if job["kind"] in {"analysis", "diagnostics"} and job["state"] in {"running", "cancelling"}}
        cpu_kinds = {job["kind"] for job in jobs if job["kind"] != "analysis" and job["state"] in {"running", "cancelling"}}
        for job in reversed(jobs):
            if job["state"] != "queued":
                continue
            if job["kind"] in {"analysis", "diagnostics"} and job["gpu"] in occupied:
                continue
            if job["kind"] in {"analysis", "diagnostics"} and "setup" in cpu_kinds:
                continue
            if job["kind"] == "setup" and occupied:
                continue
            if job["kind"] != "analysis" and job["kind"] in cpu_kinds:
                continue
            if not self._spawn(job):
                continue
            if job["kind"] in {"analysis", "diagnostics"}:
                occupied.add(job["gpu"])
            else:
                cpu_kinds.add(job["kind"])

    def _progress(self, job: dict) -> dict:
        from .eta import RuntimeEstimator
        if not hasattr(self, "_runtime_estimator"):
            self._runtime_estimator = RuntimeEstimator()
        eta = self._runtime_estimator.estimate(job)
        if not job.get("run_dir"):
            return {}
        path = Path(job["run_dir"]) / "run_state.json"
        try:
            state = self._runtime_estimator.metadata_cache.read(path)
            if not isinstance(state, dict) or not isinstance(state.get("phases", {}), dict):
                raise ValueError("진행 상태 형식 오류")
            phases = state.get("phases", {})
            stages = {"complex": ("input", "prediction", "final"), "design": ("target", "design", "tier2", "final"), "screen": ("target", "screen", "final"), "mature": ("target", "mature", "final")}[job["mode"]]
            execution = state.get("execution", {})
            # These files are observations, not queue control inputs. A
            # malformed nested record must not abort this tick and starve
            # otherwise independent queued work on another GPU.
            if (not all(isinstance(value, dict) for value in phases.values())
                    or not isinstance(execution, dict)):
                raise ValueError("진행 단계/실행 상태 형식 오류")
            completed_phases = execution.get("completed_phases", list(phases))
            if (not isinstance(completed_phases, list)
                    or not all(isinstance(value, str) for value in completed_phases)):
                raise ValueError("완료 단계 목록 형식 오류")
            if job.get("resume") and (execution.get("state") != "running" or "completed_phases" not in execution):
                return {"phase": "resume_validation", "progress": {"completed": None, "total": len(stages)}, "eta": eta}
            completed = set(completed_phases) if execution.get("state") == "running" else set(phases)
            if job["mode"] == "complex":
                from .eta import complex_model_observations
                observation = complex_model_observations(job, state=state, metadata_cache=self._runtime_estimator.metadata_cache)
                return {"phase": "prediction" if "final" not in completed else "complete",
                        "progress": observation["progress"], "eta": eta}
            if state.get("current_phase") is not None and not isinstance(state["current_phase"], str):
                raise ValueError("현재 단계 이름은 문자열이어야 합니다.")
            phase = state.get("current_phase") or next((name for name in stages if name not in completed), "complete")
            comparison_path = Path(job["run_dir"]) / "02_comparison/comparison_status.json"
            if comparison_path.is_file():
                comparison = json.loads(comparison_path.read_text())
                if not isinstance(comparison, dict):
                    raise ValueError("엔진 비교 진행 상태는 객체여야 합니다.")
                rows = comparison.get("rows")
                total = comparison.get("expected_rows")
                if (not isinstance(rows, list) or type(total) is not int or total < 0
                        or len(rows) > total or any(not isinstance(row, dict)
                            or not isinstance(row.get("status"), str)
                            or row["status"] not in {"complete", "failed"} for row in rows)):
                    raise ValueError("엔진 비교 진행 상태 형식 오류")
                return {"phase": "engine_comparison" if comparison.get("status") == "running" else phase,
                        "progress": {"completed": len(rows), "total": total,
                                     "succeeded": sum(row["status"] == "complete" for row in rows),
                                     "failed": sum(row["status"] == "failed" for row in rows)}, "eta": eta}
            if phase in {"colabfold_screen", "boltz_rescore"} and eta.get("total") is not None:
                return {"phase": phase, "progress": {"completed": eta.get("completed", 0), "total": eta["total"]}, "eta": eta}
            return {"phase": phase, "progress": {"completed": sum(name in completed for name in stages), "total": len(stages)}, "eta": eta}
        except FileNotFoundError:
            return {"eta": eta}
        except (ValueError, OSError):
            return {"phase": "state_unreadable", "error": "진행 상태 파일을 읽지 못했습니다. 로그를 확인하세요.",
                    "eta": {**eta, "status": "unavailable", "remaining_seconds": None, "lower_seconds": None, "upper_seconds": None}}

    def _loop(self):
        import fcntl
        self.lockfile = (self.store.root / "supervisor.lock").open("a+")
        while not self.stop_event.is_set():
            try:
                fcntl.flock(self.lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                # Standby servers can take over after the current supervisor exits.
                self.stop_event.wait(self.interval)
        else:
            self.lockfile.close()
            return
        try:
            while not self.stop_event.is_set():
                try:
                    self._tick()
                except Exception as exc:
                    print(f"[platform queue] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                self.stop_event.wait(self.interval)
        finally:
            fcntl.flock(self.lockfile.fileno(), fcntl.LOCK_UN)
            self.lockfile.close()
