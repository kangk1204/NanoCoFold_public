"""Exclude managed application updates while a runtime or submission is active."""
from __future__ import annotations

import contextlib
from contextvars import ContextVar
import fcntl
import json
import os
from pathlib import Path
import stat
import sys

_CORE_LEASES = ContextVar("nanocofold_core_leases", default=())
CORE_LEASE_ENV = "NANOCOFOLD_CORE_LEASES"


@contextlib.contextmanager
def _bind_core_lease(descriptor, workspace):
    leases = tuple(dict.fromkeys((*_CORE_LEASES.get(), (descriptor, str(workspace)))))
    token = _CORE_LEASES.set(leases)
    try:
        yield
    finally:
        _CORE_LEASES.reset(token)


def core_lease_handoffs():
    """Validated capabilities for child environments; never mutate os.environ."""
    leases = _CORE_LEASES.get()
    if not leases:
        encoded = os.environ.get(CORE_LEASE_ENV)
        if encoded is None:
            return ()
        try:
            if len(encoded) > 16384:
                raise ValueError("Core lease metadata exceeds its bound.")
            leases = json.loads(encoded)
        except (TypeError, ValueError) as error:
            raise ValueError("Cannot verify inherited application lease.") from error
    if not isinstance(leases, (list, tuple)) or not 1 <= len(leases) <= 8:
        raise ValueError("Cannot verify inherited application lease.")
    checked = []
    for lease in leases:
        probe = None
        try:
            if (not isinstance(lease, (list, tuple)) or len(lease) != 2
                    or type(lease[0]) is not int or not isinstance(lease[1], str)):
                raise ValueError("Invalid application lease shape.")
            descriptor, location = lease
            workspace = Path(location)
            if not workspace.is_absolute() or _core_installation(workspace, complete=False) is None:
                raise ValueError("Application lease owner is not a managed installation.")
            path = workspace / ".platform/public-install.lock"
            identity = os.fstat(descriptor)
            current = path.stat(follow_symlinks=False)
            if (not stat.S_ISREG(identity.st_mode) or identity.st_uid != os.getuid() or identity.st_nlink != 1
                    or not stat.S_ISREG(current.st_mode)
                    or (identity.st_dev, identity.st_ino) != (current.st_dev, current.st_ino)):
                raise ValueError("Application lease inode differs from its owner.")
            probe = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            opened = os.fstat(probe)
            if (opened.st_dev, opened.st_ino) != (identity.st_dev, identity.st_ino):
                raise ValueError("Application lease inode changed.")
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise ValueError("Application lease is not held.")
            try:
                fcntl.flock(probe, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                # An exclusive handoff must already belong to this open-file
                # description; never downgrade it to a shared reservation.
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                # The probe prevents a writer while the transferred descriptor
                # acquires its own shared reservation. Another reader's lock
                # alone never authorizes an unlocked descriptor's handoff.
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            checked.append((descriptor, str(workspace)))
        except (OSError, ValueError) as error:
            raise ValueError("응용 프로그램 잠금 인계를 확인할 수 없습니다. / Cannot verify application lease handoff.") from error
        finally:
            if probe is not None:
                os.close(probe)
    return tuple(dict.fromkeys(checked))


def _core_installation(workspace, *, complete=True):
    """An absent marker is an older/unmanaged installation, not a failed one."""
    workspace = Path(workspace).resolve()
    marker = workspace / ".platform/public-core-install.json"
    if not marker.exists() and not marker.is_symlink():
        return None
    try:
        record = json.loads(marker.read_text())
    except (OSError, ValueError) as error:
        raise ValueError("관리 설치 기록을 확인할 수 없습니다. 원래 bash install.sh 명령을 다시 실행하세요. / Cannot verify installation; rerun the original bash install.sh command.") from error
    if (not isinstance(record, dict) or record.get("workspace") != str(workspace)
            or record.get("python") != str(workspace / ".venv-dev/bin/python")):
        raise ValueError("관리 설치 위치가 일치하지 않습니다. 기존 파일을 보존하고 bash install.sh 설정을 확인하세요. / Managed installation identity differs; check bash install.sh settings.")
    if complete and record.get("status") != "complete":
        raise ValueError("설치가 완료되지 않았습니다. 원래 bash install.sh 명령을 다시 실행하세요. / Installation is incomplete; rerun the original bash install.sh command.")
    return record


def _installer_handoff(workspace):
    """Validate the inherited exclusive reservation without downgrading it."""
    path = workspace / ".platform/public-install.lock"
    probe = None
    try:
        descriptor = int(os.environ["NANOCOFOLD_INSTALL_LOCK_FD"])
        identity = os.fstat(descriptor)
        current = path.stat(follow_symlinks=False)
        if (not stat.S_ISREG(identity.st_mode) or identity.st_uid != os.getuid() or identity.st_nlink != 1
                or not stat.S_ISREG(current.st_mode)
                or (identity.st_dev, identity.st_ino) != (current.st_dev, current.st_ino)):
            raise ValueError("Installation lock descriptor does not match its owner.")
        # A shared or unlocked descriptor is not an installer handoff. Testing
        # a separate open-file description cannot downgrade the parent's flock.
        probe = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        opened = os.fstat(probe)
        if (opened.st_dev, opened.st_ino) != (identity.st_dev, identity.st_ino):
            raise ValueError("Installation lock inode changed.")
        try:
            fcntl.flock(probe, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise ValueError("Installation handoff is not exclusively locked.")
        # If somebody else owns the exclusive lock, this fails. When it is our
        # inherited open-file description, the operation preserves its lock.
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except (OSError, ValueError, KeyError) as error:
        raise ValueError("설치 잠금 인계를 확인할 수 없습니다. 원래 bash install.sh 명령을 다시 실행하세요. / Cannot verify installation lock handoff; rerun bash install.sh.") from error
    finally:
        if probe is not None:
            os.close(probe)


@contextlib.contextmanager
def installed_core_lease(command=None):
    """Reserve the interpreter's managed core, independently of output roots."""
    prefix = Path(sys.prefix).resolve()
    if prefix.name != ".venv-dev":
        yield
        return
    workspace = prefix.parent
    marker = workspace / ".platform/public-core-install.json"
    if not marker.exists() and not marker.is_symlink():
        yield  # Existing source and unmanaged environments remain supported.
        return
    if command in {"init", "setup"} and "NANOCOFOLD_INSTALL_LOCK_FD" in os.environ:
        _core_installation(workspace, complete=False)
        descriptor = _installer_handoff(workspace)
        with _bind_core_lease(descriptor, workspace):
            yield  # The inherited descriptor remains exclusively held until exit.
        return
    with maintenance_lock(workspace) as descriptor, _bind_core_lease(descriptor, workspace):
        yield


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
        _core_installation(workspace)
        current = root / "core-update-state.json"
        try:
            state = json.loads(current.read_text())
        except FileNotFoundError:
            state = {"status": "complete"}  # Older installations remain usable.
        except (OSError, ValueError) as error:
            raise ValueError("업데이트 상태를 확인할 수 없습니다. bash update.sh를 다시 실행하세요. / Cannot verify update state; rerun bash update.sh.") from error
        if not isinstance(state, dict) or state.get("status") != "complete":
            raise ValueError("업데이트가 완료되지 않았습니다. bash update.sh를 다시 실행하세요. / Update is incomplete; rerun bash update.sh.")
        yield descriptor
    finally:
        # Never unlink the inode: every process must coordinate on the same lock.
        os.close(descriptor)
