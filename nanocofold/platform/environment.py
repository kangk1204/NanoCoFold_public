"""Reuse an explicit installation check, never an input-review or prediction result.

This is a UI readiness receipt. The CLI's full per-run preflight stays authoritative.
No imports of engine environments, GPU operations, or network calls occur here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
from pathlib import Path

from ..config import cfg_get, resolve_path
from ..diagnostics import _engine_extra_env, _engine_spec, _followup_engines, _mode_engines
from .inputs import RUNTIME_PATHS


def installation_override(values: dict | None) -> dict:
    """Installation probes never prepare MSAs or confer transmission consent.

    Store an inert, local-only MSA configuration; the reviewed prediction
    configuration is not modified and still requires fresh sequence consent.
    """
    if values is not None and not isinstance(values, dict):
        raise ValueError("설정은 객체여야 합니다.")
    cfg = copy.deepcopy(values or {})
    for name in ("run", "screen", "complex"):
        if name in cfg and not isinstance(cfg[name], dict):
            raise ValueError(name + " 설정은 객체여야 합니다.")
    for name in ("colabfold", "comparison"):
        if name in cfg.get("screen", {}) and not isinstance(cfg["screen"][name], dict):
            raise ValueError("screen." + name + " 설정은 객체여야 합니다.")
    cfg.setdefault("screen", {})["allow_public_msa_server"] = False
    cfg["screen"].setdefault("colabfold", {})["msa_source"] = "cache"
    if cfg.get("run", {}).get("mode") == "complex" and cfg["screen"].get("comparison", {}).get("enabled"):
        cfg.setdefault("complex", {}).setdefault("comparison_engines", cfg["screen"]["comparison"]["engines"])
        cfg["complex"].setdefault("msa", "precomputed")
        cfg["screen"]["comparison"]["enabled"] = False
    if "complex" in cfg:
        cfg["complex"]["msa_source"] = "cache"
        cfg["complex"]["allow_public_msa_server"] = False
    return cfg


def _identity_file(path: Path) -> dict:
    try:
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    except OSError:
        return {"path": str(path), "missing": True}


def _stamp(path: Path) -> dict:
    try:
        st = path.stat()
        return {"path": str(path), "real": str(path.resolve()), "device": st.st_dev,
                "inode": st.st_ino, "size": st.st_size, "mtime": st.st_mtime_ns,
                "ctime": st.st_ctime_ns}
    except OSError:
        return {"path": str(path), "missing": True}


def environment_scope(workspace: Path, cfg: dict, *, gpu: str | None = None) -> dict:
    """Bind a check to installation/GPU/engine identities, not protein sequences."""
    from .. import __version__
    root = Path(workspace).resolve()
    engines = sorted(set(_mode_engines(cfg, cfg["run"]["mode"]) +
                         _followup_engines(cfg, cfg["run"]["mode"])))
    locations = {}
    paths = set()
    for name in RUNTIME_PATHS:
        value = cfg_get(cfg, name)
        if value:
            path = resolve_path(cfg, value)
            locations[name] = str(path)
            paths.add(path)
    specs = {}
    for engine in engines:
        spec = _engine_spec(cfg, engine)
        env = resolve_path(cfg, spec.get("env"))
        specs[engine] = {"spec": spec, "environment": _engine_extra_env(cfg, engine)}
        if env:
            paths.update([env, env / "pyvenv.cfg", env / "bin/python"])
            paths.update((env / "lib").glob("python*/site-packages"))
    paths.update((root / ".platform").glob("setup-*.json"))
    paths.update(root / name for name in (".platform/public-core-install.json",
                 ".platform/model-assets.json", "config/environment.yaml", "models"))
    # Model/configuration changes and source updates invalidate the receipt. We do
    # not rehash model archives here; the unmodified native preflight verifies them.
    package = Path(__file__).resolve().parents[1]
    source = {str(p.relative_to(package)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(package.rglob("*.py")) if "__pycache__" not in p.parts}
    payload = {"schema": 1, "version": __version__, "source": source,
               "machine": platform.node(), "workspace": str(root), "engines": engines,
               "gpu": gpu if gpu is not None else os.environ.get("NANOCOFOLD_GPU", "0"),
               "driver": _identity_file(Path("/proc/driver/nvidia/version")),
               "gpu_devices": [_identity_file(p) for p in sorted(Path("/proc/driver/nvidia/gpus").glob("*/information"))],
               "locations": locations, "specs": specs,
               "esm_model": cfg_get(cfg, "mature.esm.model"),
               "paths": [_stamp(p) for p in sorted(paths)]}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {"digest": hashlib.sha256(encoded.encode()).hexdigest(), "engines": engines}


def readiness(workspace: Path, cfg: dict, store, *, gpu: str | None = None) -> dict:
    scope = environment_scope(workspace, cfg, gpu=gpu)
    for job in store.list(kind="diagnostics", limit=1000):
        report = job.get("diagnostics") or {}
        if (job.get("kind") != "diagnostics" or not job.get("environment_only") or
                (report.get("environment_scope") or job.get("environment_scope") or {}).get("digest") != scope["digest"]):
            continue
        passed = job.get("state") == "complete" and report.get("ok") is True
        return {"ready": passed, "scope": scope, "job_id": job["id"],
                "state": job["state"], "checked_at": job.get("finished"),
                "diagnostics": report, "reason": "passed" if passed else "check_not_passed"}
    return {"ready": False, "scope": scope, "job_id": None, "reason": "not_checked"}
