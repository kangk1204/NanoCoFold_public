#!/usr/bin/env python3
"""Seal byte-identical shared application cores in two explicit source trees.

Distribution policy JSON contains operator/fixture choices and remains separate.
Normal public tests validate their own manifest without requiring another repo.
"""
import argparse
import hashlib
import json
from pathlib import Path


SUFFIXES = {".py", ".js", ".j2", ".html", ".css"}


def core_files(root):
    package = root / "nanocofold"
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(package.rglob("*"))
            if p.is_file() and not p.is_symlink() and p.suffix in SUFFIXES
            and "__pycache__" not in p.parts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--peer", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    peer = args.peer.resolve()
    if peer == root or not (peer / "pyproject.toml").is_file():
        parser.error("--peer must identify the other explicit source repository")
    own, other = core_files(root), core_files(peer)
    if own != other:
        mismatch = sorted(name for name in set(own) | set(other) if own.get(name) != other.get(name))
        raise ValueError("Unresolved shared core drift: " + ", ".join(mismatch))
    data = {"schema_version": 1, "scope": "Byte-identical executable application core; distribution policy and preset JSON remain explicit data.",
            "policy_data": ["nanocofold/_data/runtime-policy.json", "nanocofold/_data/public-msa-presets.json"],
            "files": own}
    content = json.dumps(data, sort_keys=True, indent=2) + "\n"
    for repository in (root, peer):
        (repository / "tests/core_sync_manifest.json").write_text(content, encoding="utf-8")
    print(json.dumps({"shared_core_files": len(own), "byte_equal": True,
                      "manifest_sha256": hashlib.sha256(content.encode()).hexdigest()}))


if __name__ == "__main__":
    main()
