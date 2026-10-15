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
from urllib.parse import unquote, urlparse

from ..config import cfg_get
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


def normalized_installation_config(workspace: Path, values: dict | None = None, *, mode: str | None = None) -> dict:
    """Resolve defaults/mode before translating an input-free installation check."""
    from ..config import _deep_merge
    from .inputs import normalized_config
    if values is not None and not isinstance(values, dict):
        raise ValueError("설정은 객체여야 합니다.")
    base = normalized_config(workspace)
    def check_shape(defaults, supplied, prefix=""):
        for key, value in supplied.items():
            expected = defaults.get(key)
            if isinstance(expected, dict):
                if not isinstance(value, dict):
                    raise ValueError(prefix + key + " 설정은 객체여야 합니다.")
                check_shape(expected, value, prefix + key + ".")
            elif isinstance(expected, bool) and type(value) is not bool:
                raise ValueError(prefix + key + " 설정은 true/false여야 합니다.")
    check_shape(base, values or {})
    cfg = _deep_merge(base, values or {})
    if mode is not None:
        cfg["run"]["mode"] = mode
    if cfg["run"]["mode"] not in {"complex", "design", "screen", "mature"}:
        raise ValueError("지원하지 않는 분석 모드입니다.")
    return normalized_config(workspace, installation_override(cfg))


def _identity_file(path: Path) -> dict:
    try:
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    except OSError:
        return {"path": str(path), "missing": True}


def _stamp(path: Path) -> dict:
    try:
        st = path.stat()
        if path.is_dir():
            # Cache/log/bytecode creation must not expire a successful check.
            # Required descendants have their own file fingerprints below.
            return {"path": str(path), "real": str(path.resolve()), "device": st.st_dev,
                    "inode": st.st_ino, "directory": True}
        return {"path": str(path), "real": str(path.resolve()), "device": st.st_dev,
                "inode": st.st_ino, "size": st.st_size, "mtime": st.st_mtime_ns,
                "ctime": st.st_ctime_ns}
    except OSError:
        return {"path": str(path), "missing": True}


def _workspace_path(root: Path, value) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def _engine_environment(root: Path, cfg: dict, engine: str) -> dict:
    extra = _engine_extra_env(cfg, engine)
    if engine == "colabfold" and "JAX_COMPILATION_CACHE_DIR" not in (cfg_get(cfg, "screen.colabfold.env_vars", {}) or {}):
        # The server may have been launched elsewhere; detached workers always
        # use this workspace as their working/project root.
        extra["JAX_COMPILATION_CACHE_DIR"] = str(root / "models/colabfold/jax_cache")
    return extra


def _tree_files(root: Path, suffixes: set[str], *, limit: int = 50000) -> list[Path]:
    """Bound local metadata discovery; never import tools or read model bodies."""
    if not root.is_dir():
        return [root]
    found, visited = [], 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in {"__pycache__", ".git", ".cache"})
        visited += len(dirs) + len(files)
        if visited > limit:
            raise ValueError("설치 파일이 환경 확인 범위를 초과합니다. 실행 환경과 모델 경로를 확인하세요.")
        found.extend(Path(folder) / name for name in sorted(files) if Path(name).suffix in suffixes)
    return found


def _runtime_files(env: Path, spec: dict) -> set[Path]:
    from ..diagnostics import _site_packages_roots
    paths = {env / "pyvenv.cfg", env / "bin/python"}
    paths.update(env / "bin" / name for name in spec.get("binaries", []))
    packages = {name.split(".")[0] for name in spec.get("imports", [])}
    for site in _site_packages_roots(env):
        for package in packages:
            paths.update(_tree_files(site / package, {".py", ".so", ".pyd", ".yaml", ".json"}))
            paths.add(site / (package + ".py"))
        paths.update(site.glob("*.pth"))
        for dist in site.glob("*.dist-info"):
            paths.update(dist / name for name in ("METADATA", "RECORD", "direct_url.json"))
            direct = dist / "direct_url.json"
            if direct.is_file() and direct.stat().st_size <= 1024 * 1024:
                try:
                    data = json.loads(direct.read_text())
                    url = urlparse(data.get("url", ""))
                    if data.get("dir_info", {}).get("editable") and url.scheme == "file":
                        checkout = Path(unquote(url.path))
                        for package in packages:
                            for source in (checkout / package, checkout / "src" / package):
                                if source.is_dir():
                                    paths.update(_tree_files(source, {".py", ".so", ".pyd", ".yaml", ".json"}))
                except (OSError, ValueError, TypeError, AttributeError):
                    pass  # Changed/malformed metadata itself still invalidates the receipt.
    return paths


def _model_files(root: Path, cfg: dict, engine: str, env: Path | None, extra: dict) -> set[Path]:
    """Name the consumed assets, including absent files, without weight hashing."""
    from .. import diagnostics as d
    def path(value):
        return _workspace_path(root, value)
    def setting(name, default):
        return path(cfg_get(cfg, name, default))
    def variable(name, default):
        return path(extra.get(name) or os.environ.get(name) or default)
    sites = d._site_packages_roots(env) if env else []
    if engine == "boltz2":
        cache_arg = next((d._arg_value(cfg_get(cfg, key, []) or [], "--cache")
                          for key in ("score.boltz2_extra_args", "structure.boltz2.extra_args")
                          if d._arg_value(cfg_get(cfg, key, []) or [], "--cache")), None)
        cache = variable("BOLTZ_CACHE", cache_arg or Path.home() / ".boltz")
        # The archive/index identifies the installed support set. Enumerating
        # the full molecule dictionary belongs to native per-run validation.
        return {cache / name for name in (*d._BOLTZ2_REQUIRED_FILES, "mols.tar", ".nanocofold-molecules.json")}
    if engine == "chai1":
        fallback = next((site / "downloads" for site in sites if (site / "downloads").exists()),
                        root / "envs/chai1/lib/python3.11/site-packages/downloads")
        downloads = variable("CHAI_DOWNLOADS_DIR", fallback)
        return {downloads / "models_v2" / name for name in d._CHAI1_REQUIRED_FILES} | {
            downloads / "conformers_v1.apkl", downloads / "esm/traced_sdpa_esm2_t36_3B_UR50D_fp16.pt"}
    if engine == "colabfold":
        data = setting("screen.colabfold.data_dir", "models/colabfold")
        return {data / "weights.json"} | {data / "params" / f"params_model_{i}_multimer_v3.npz" for i in range(1, 6)}
    if engine == "boltzgen":
        from ..design_boltzgen import MODEL_NAMES
        return {setting("design.boltzgen.cache_dir", "models/boltzgen") / name for name in MODEL_NAMES}
    if engine == "opendde":
        from ..opendde import assets
        checkpoint = cfg_get(cfg, "screen.opendde.checkpoint", "opendde_abag.pt")
        return {setting("screen.opendde.data_dir", "models/opendde") / name for name in assets()
                if not name.startswith("checkpoint/") or name.endswith("/" + checkpoint)}
    if engine == "esm":
        cache = variable("TORCH_HOME", variable("XDG_CACHE_HOME", Path.home() / ".cache") / "torch")
        model = str(cfg_get(cfg, "mature.esm.model", "esm2_t33_650M_UR50D"))
        return {cache / "hub/checkpoints" / (model + suffix) for suffix in (".pt", "-contact-regression.pt")}
    if engine == "antifold":
        explicit = cfg_get(cfg, "mature.antifold.model_path")
        return {path(explicit)} if explicit else {root / "models/antifold/model.pt", *(site / "models/model.pt" for site in sites)}
    if engine == "anarci":
        return {site / "anarci/dat/HMMs" / ("ALL.hmm" + suffix) for site in sites for suffix in ("", ".h3f", ".h3i", ".h3m", ".h3p")}
    if engine == "rfantibody":
        source = variable("RFANTIBODY_ROOT", root / "tools/RFantibody")
        weights = variable("RFANTIBODY_WEIGHTS", source / "weights")
        return {weights / name for name in d._RFANTIBODY_REQUIRED_WEIGHTS} | {
            root / "tools/RFantibody/include/weights/RFdiffusion_Ab.pt"}
    return set()


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
            path = _workspace_path(root, value)
            locations[name] = str(path)
            paths.add(path)
    specs = {}
    for engine in engines:
        spec = _engine_spec(cfg, engine)
        env = _workspace_path(root, spec["env"]) if spec.get("env") else None
        extra = _engine_environment(root, cfg, engine)
        specs[engine] = {"spec": spec, "environment": extra}
        if env:
            paths.update([env, env / "pyvenv.cfg", env / "bin/python"])
            paths.update(_runtime_files(env, spec))
        paths.update(_model_files(root, cfg, engine, env, extra))
        if engine == "rfantibody":
            source_root = extra.get("RFANTIBODY_ROOT") or os.environ.get("RFANTIBODY_ROOT") or root / "tools/RFantibody"
            for folder in ("src", "include"):
                paths.update(_tree_files(_workspace_path(root, source_root) / folder, {".py"}))
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
