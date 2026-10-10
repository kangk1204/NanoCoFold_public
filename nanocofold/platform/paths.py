from __future__ import annotations

import hashlib
import math
from pathlib import Path


def contained(root: Path, value: str | Path, *, exists: bool = False) -> Path:
    """Resolve symlinks before checking boundaries; never expose arbitrary host files."""
    root = root.resolve()
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root):
        raise ValueError("파일은 현재 작업 공간 안에 있어야 합니다.")
    if exists and not path.exists():
        raise FileNotFoundError(f"파일이 없습니다: {path.name}")
    return path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def native(value):
    if isinstance(value, dict):
        return {str(k): native(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [native(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        return native(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
