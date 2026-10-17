from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np


def to_native(obj: Any) -> Any:
    """numpy/pandas 스칼라·배열을 YAML/JSON 직렬화 가능한 Python 객체로 변환."""
    if isinstance(obj, dict):
        return {str(key): to_native(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_native(value) for value in obj]
    if isinstance(obj, np.ndarray):
        return to_native(obj.tolist())
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        value = float(obj)
        return value if np.isfinite(value) else None
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, float) and not np.isfinite(obj):
        return None
    return obj


def format_seconds(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
