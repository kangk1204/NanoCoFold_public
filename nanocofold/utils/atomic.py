"""Durable single-file publication; callers own the run lock and transaction order."""
from __future__ import annotations

import json
import os
import logging
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


def check_output(path: Path, root: Path | None = None) -> None:
    path = Path(os.path.abspath(path))
    root = Path(os.path.abspath(root if root is not None else path.parent))
    if not path.is_relative_to(root):
        raise OSError(f"출력은 선택한 디렉터리 내부여야 합니다: {path}")
    # Trust the selected root's ancestors (e.g. macOS /tmp -> /private/tmp).
    # A link at the root or below it must never redirect this publication.
    cursor = path
    while True:
        if cursor.is_symlink():
            raise OSError(f"출력의 심볼릭 링크를 덮어쓸 수 없습니다: {cursor}")
        if cursor == root:
            break
        cursor = cursor.parent


@contextmanager
def atomic_writer(path: Path, *, root: Path | None = None, prefix: str = ".publish-"):
    path = Path(os.path.abspath(path))
    check_output(path, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.parent / (prefix + uuid4().hex)
    created = False
    try:
        descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        created = True
        try:
            handle = os.fdopen(descriptor, "wb")
        except BaseException:
            os.close(descriptor)
            raise
        with handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        check_output(path, root)
        staged.replace(path)
        try:
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            # The file has already been replaced. Expose reduced durability
            # without falsely reporting that publication itself failed.
            logging.getLogger(__name__).warning("파일 발행 완료, 디렉터리 동기화 미확인: %s: %s", path, exc)
    finally:
        if created:
            staged.unlink(missing_ok=True)


def write_bytes(path: Path, content: bytes, **kwargs) -> None:
    with atomic_writer(path, **kwargs) as handle:
        handle.write(content)


def write_json(path: Path, payload, **kwargs) -> None:
    content = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    write_bytes(path, content.encode("utf-8"), **kwargs)


def write_csv(path: Path, frame, *, encoding="utf-8", **kwargs) -> None:
    """Publish formula-safe text without mutating the caller's scientific frame.

    Input IDs must already satisfy the input contract. Text escaping is a final
    output defense, not an ID alias or a transformation of numerical scores.
    Canonical run tables use UTF-8 without BOM for machine readers. Excel-facing
    download/synthesis exports explicitly request UTF-8 BOM or use XLSX.
    """
    from .spreadsheet import csv_cell, csv_headers
    from pandas import CategoricalDtype
    from pandas.api.types import is_string_dtype
    safe = frame.copy()
    headers = csv_headers(safe.columns)
    for column in safe.columns:
        if (safe[column].dtype == object or is_string_dtype(safe[column].dtype)
                or isinstance(safe[column].dtype, CategoricalDtype)):
            safe[column] = safe[column].map(csv_cell)
    safe.columns = headers
    # pandas streams into a binary handle; avoid materializing large CSV strings.
    with atomic_writer(path, **kwargs) as handle:
        safe.to_csv(handle, index=False, encoding=encoding)
