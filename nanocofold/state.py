from __future__ import annotations

import json
import fcntl
import os
import socket
import stat
from pathlib import Path

from .utils.misc import to_native
from .utils.atomic import write_json

STATE_FILE = "run_state.json"


class CorruptRunState(ValueError):
    """Existing evidence is invalid and must be repaired explicitly."""


class RunLockedError(RuntimeError):
    pass


class RunLock:
    """One writer per run, shared by CLI and platform workers (Linux/macOS)."""

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir).resolve()
        self.path = self.run_dir / ".run.lock"
        self.handle = None

    def __enter__(self):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise ValueError(f"잠금 파일 소유자/형식이 올바르지 않습니다: {self.path}")
            self.handle = os.fdopen(descriptor, "r+", encoding="utf-8")
        except BaseException:
            os.close(descriptor)
            raise
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.handle.close()
            self.handle = None
            raise RunLockedError(f"다른 프로세스가 실행 중입니다: {self.run_dir}") from exc
        except BaseException:
            self.handle.close()
            self.handle = None
            raise
        try:
            self.handle.seek(0)
            self.handle.truncate()
            self.handle.write(json.dumps({"pid": os.getpid(), "host": socket.gethostname()}))
            self.handle.flush()
        except BaseException:
            # __exit__ is never entered when __enter__ fails. Release the
            # acquired inode so a storage failure does not strand recovery.
            self.handle.close()
            self.handle = None
            raise
        return self

    def __exit__(self, *args):
        if self.handle:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None
        # Keep inode: unlinking lets a third writer acquire a different lock.


class RunState:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir).resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / STATE_FILE
        self.data: dict = {"phases": {}, "warnings": []}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CorruptRunState(f"손상된 {self.path}: {exc}. 원본을 보존하고 상태를 복구하세요.") from exc
            if (not isinstance(self.data, dict)
                    or not isinstance(self.data.get("phases", {}), dict)
                    or not all(isinstance(v, dict) for v in self.data.get("phases", {}).values())
                    or not isinstance(self.data.get("warnings", []), list)
                    or not all(isinstance(v, str) for v in self.data.get("warnings", []))):
                raise CorruptRunState(f"손상된 {self.path}: phases/warnings 형식이 올바르지 않습니다.")
        self.data.setdefault("phases", {})
        self.data.setdefault("warnings", [])

    def save(self) -> None:
        """부분 기록으로 run_state.json 이 깨져 단계 기록이 통째로 사라지지 않도록 원자적으로 저장한다."""
        write_json(self.path, to_native(self.data), root=self.run_dir)

    def set_phase(self, name: str, payload: dict) -> None:
        self.data["phases"][name] = to_native(payload)
        execution = self.data.get("execution", {})
        if execution.get("state") == "running" and "completed_phases" in execution:
            completed = execution["completed_phases"]
            if name not in completed: completed.append(name)
            self.data["current_phase"] = None
        self.save()

    def begin_phase(self, name: str) -> None:
        self.data["current_phase"] = name
        self.save()

    def get_phase(self, name: str) -> dict:
        return self.data["phases"].get(name, {})

    def has_phase(self, name: str) -> bool:
        return name in self.data["phases"]

    def add_warnings(self, messages) -> None:
        """문자열 또는 문자열 목록을 경고에 추가한다 (문자열은 문자 단위로 쪼개지 않는다)."""
        if isinstance(messages, str):
            messages = [messages]
        for message in messages or []:
            if message and message not in self.data["warnings"]:
                self.data["warnings"].append(message)
        self.save()

    @property
    def warnings(self) -> list[str]:
        return list(self.data["warnings"])
