"""Enumerate current prediction manifests without following archive or symlink trees."""
from pathlib import Path
import os

ARCHIVE_DIRECTORIES = {".history", ".compact", "source_snapshot"}


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
