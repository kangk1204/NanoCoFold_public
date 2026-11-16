"""Enumerate current prediction manifests without following archive or symlink trees."""
from pathlib import Path
import contextlib
import contextvars
import hashlib
import os

ARCHIVE_DIRECTORIES = {".history", ".compact", "source_snapshot"}
_ATTEMPT = contextvars.ContextVar("prediction_manifest_attempt", default=None)


@contextlib.contextmanager
def prediction_attempt(root, configuration=None):
    """Collect only manifests actually produced or validated by this attempt."""
    token = _ATTEMPT.set({"root": Path(root).resolve(), "manifests": {}, "configuration": dict(configuration or {})})
    try:
        yield
    finally:
        _ATTEMPT.reset(token)


def record_manifest(path):
    attempt = _ATTEMPT.get()
    if attempt is None:
        return
    path = Path(path)
    relative = path.resolve().relative_to(attempt["root"]).as_posix()
    if path.is_symlink():
        raise ValueError("예측 단계 기록은 심볼릭 링크일 수 없습니다.")
    attempt["manifests"][relative] = {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def attempt_manifests(root):
    attempt = _ATTEMPT.get()
    if attempt is None or attempt["root"] != Path(root).resolve():
        return None
    records = []
    for item in attempt["manifests"].values():
        path = attempt["root"] / item["path"]
        # A stage may commit a compact checkpoint after inference. Refresh only
        # paths that this attempt actually used; never discover old stages here.
        value = dict(item)
        if path.is_file() and not path.is_symlink():
            value["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        records.append(value)
    return records


def attempt_configuration(root):
    attempt = _ATTEMPT.get()
    if attempt is None or attempt["root"] != Path(root).resolve():
        return None
    return dict(attempt["configuration"])


def current_manifests(root, warn=None):
    root = Path(root).resolve()
    for directory, children, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in list(children):
            path = parent / name
            if name in ARCHIVE_DIRECTORIES or path.is_symlink():
                children.remove(name)
                if warn and path.is_symlink():
                    warn(f"예측 목록의 심볼릭 폴더를 제외했습니다: {path.relative_to(root)}")
        if "predictions.csv" in files:
            path = parent / "predictions.csv"
            if path.is_symlink():
                if warn:
                    warn(f"예측 목록의 심볼릭 파일을 제외했습니다: {path.relative_to(root)}")
                continue
            yield path
