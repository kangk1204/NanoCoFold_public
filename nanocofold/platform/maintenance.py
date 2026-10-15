"""Exclude managed application updates while a runtime or submission is active."""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path
import stat


@contextlib.contextmanager
def maintenance_lock(workspace: str | Path):
    """Keep the installed core stable; failed updates require an explicit retry."""
    root = Path(workspace) / ".platform"
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(root / "public-install.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError("설치 잠금 파일의 소유자 또는 형식이 올바르지 않습니다. / Invalid installation lock.")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("설치 또는 업데이트 중입니다. 완료 후 다시 시도하세요. / Installation or update is in progress; retry when it finishes.") from error
        current = root / "core-update-state.json"
        try:
            state = json.loads(current.read_text())
        except FileNotFoundError:
            state = {"status": "complete"}  # Older installations remain usable.
        except (OSError, ValueError) as error:
            raise ValueError("업데이트 상태를 확인할 수 없습니다. bash update.sh를 다시 실행하세요. / Cannot verify update state; rerun bash update.sh.") from error
        if not isinstance(state, dict) or state.get("status") != "complete":
            raise ValueError("업데이트가 완료되지 않았습니다. bash update.sh를 다시 실행하세요. / Update is incomplete; rerun bash update.sh.")
        yield
    finally:
        # Never unlink the inode: every process must coordinate on the same lock.
        os.close(descriptor)
