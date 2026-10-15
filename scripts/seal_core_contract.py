#!/usr/bin/env python3
"""Compare and seal shared runtime code, scientific settings and dependency pins.

Comments and distribution provenance descriptions are not runtime identity.
Operator destinations and public MSA deployment policy remain explicit data.
"""
import argparse
import hashlib
import json
from pathlib import Path

import yaml

SUFFIXES = {".py", ".js", ".j2", ".html", ".css"}
POLICY_DATA = ["nanocofold/_data/runtime-policy.json", "nanocofold/_data/public-msa-presets.json"]


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def core_files(root):
    package = root / "nanocofold"
    files = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(package.rglob("*"))
             if p.is_file() and not p.is_symlink() and p.suffix in SUFFIXES
             and "__pycache__" not in p.parts}
    # These files are consumed by the same runtime, even though they are data.
    for name in ("config/default.yaml", "nanocofold/_data/starter-presets.json",
                 "nanocofold/_data/model-assets.json", "nanocofold/_data/opendde-assets.json",
                 "nanocofold/_data/rfantibody-weights.sha256", "nanocofold/_data/core-linux.txt"):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing shared runtime contract input: " + name)
        if path.suffix == ".yaml":
            value = yaml.safe_load(path.read_text())
        elif path.suffix == ".json":
            value = json.loads(path.read_text())
            if name.endswith("/model-assets.json"):
                value = {k: v for k, v in value.items() if k not in {"recorded_date", "hash_source"}}
        else:
            value = [line.strip() for line in path.read_text().splitlines()
                     if line.strip() and not line.lstrip().startswith("#")]
        files[name] = _digest(value)
    for path in sorted((package / "_data/locks").glob("*")):
        if path.suffix not in {".in", ".txt", ".json"}:
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError("Invalid dependency contract input: " + str(path))
        if path.suffix == ".json":
            value = {k: v for k, v in json.loads(path.read_text()).items()
                     if k not in {"origin", "dependency_lock_scope"}}
            # Local editable-install locations are provenance, not install pins.
            for package_record in value.get("packages", []):
                direct = package_record.get("direct_url")
                if isinstance(direct, dict) and str(direct.get("url", "")).startswith("file:"):
                    package_record["direct_url"] = None
        else:
            value = [line.strip() for line in path.read_text().splitlines()
                     if line.strip() and not line.lstrip().startswith("#")]
        files[path.relative_to(root).as_posix()] = _digest(value)
    return files


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
    data = {"schema_version": 2, "scope": "Byte-identical executable core plus semantic scientific settings, asset pins and dependency locks; comments/provenance descriptions excluded.",
            "policy_data": POLICY_DATA, "files": own}
    content = json.dumps(data, sort_keys=True, indent=2) + "\n"
    for repository in (root, peer):
        (repository / "tests/core_sync_manifest.json").write_text(content, encoding="utf-8")
    print(json.dumps({"shared_core_files": len(own), "byte_equal": True,
                      "manifest_sha256": hashlib.sha256(content.encode()).hexdigest()}))


if __name__ == "__main__":
    main()
