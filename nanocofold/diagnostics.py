from __future__ import annotations

import json
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
from pathlib import Path
from typing import Any

from . import __version__
from .config import PROJECT_ROOT, cfg_get, resolve_path
from .startup_limits import READINESS_TIMEOUT_SECONDS

# PROJECT_ROOT 는 사용자 작업 디렉터리이므로 패키지 스크립트는 패키지 기준으로 찾는다(R9-4).
_SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"


def doctor(cfg: dict, smoke: bool = False, *, environment_only: bool = False) -> dict:
    """Return structured, JSON-serializable readiness diagnostics.

    The default path is non-mutating: it checks active mode dependencies by
    importing tools inside their configured envs, probing torch/CUDA kernels
    when torch is installed, checking required binaries/weights, and recording
    evidence rather than installing or downloading anything.
    """
    if environment_only and smoke:
        raise ValueError("Installation checks do not execute prediction smoke tests")
    mode = str(cfg_get(cfg, "run.mode", "design")).lower()
    evidence: dict[str, Any] = {
        "project_root": str(PROJECT_ROOT),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "smoke_requested": bool(smoke),
    }
    checks: list[dict[str, Any]] = []
    engines: dict[str, Any] = {}

    if mode != "complex" or environment_only:
        from .report import viewer_asset_readiness
        viewer = viewer_asset_readiness(cfg)
        evidence["report_viewer"] = viewer
        checks.append(_check("report.viewer_asset", bool(viewer["ok"]), True, viewer["stage"], viewer))

    from .report import delivery_asset_readiness
    delivery = delivery_asset_readiness(cfg)
    evidence["report_delivery"] = delivery
    checks.append(_check("report.delivery_assets", bool(delivery["ok"]), True, delivery["stage"], delivery))

    gpu = _probe_gpu()
    evidence["gpu"] = gpu
    checks.append(_check("gpu", bool(gpu.get("available")), True, "nvidia-smi", gpu))

    active_engines = _mode_engines(cfg, mode)
    followup_engines = [engine for engine in _followup_engines(cfg, mode) if engine not in active_engines]
    evidence["followup_engines"] = followup_engines
    for engine in active_engines:
        report = _probe_engine(cfg, engine, gpu, environment_only=True) if environment_only else _probe_engine(cfg, engine, gpu)
        engines[engine] = report
        checks.extend(report["checks"])
    for engine in followup_engines:
        report = _probe_followup_engine(cfg, engine, gpu, environment_only=True) if environment_only else _probe_followup_engine(cfg, engine, gpu)
        engines[engine] = report
        checks.extend(report["checks"])

    smoke_report = _smoke(cfg, mode, active_engines + followup_engines, engines) if smoke else _skipped_smoke()
    evidence["smoke"] = smoke_report
    if smoke:
        checks.append(_check("smoke", bool(smoke_report.get("ok")), True, smoke_report.get("stage", "smoke"), smoke_report))

    ok = all(check.get("ok") for check in checks if check.get("required", False))
    return _jsonable({"application_version": __version__, "ok": ok, "checks": checks, "engines": engines, "evidence": evidence,
                      **({"environment_only": True} if environment_only else {})})


def runtime_provenance(cfg: dict) -> dict:
    """Return engine versions and discovered model/weight file identities."""
    mode = str(cfg_get(cfg, "run.mode", "design")).lower()
    engines = _mode_engines(cfg, mode)
    engines += [engine for engine in _followup_engines(cfg, mode) if engine not in engines]
    manifest: dict[str, Any] = {}
    for engine in engines:
        spec = _engine_spec(cfg, engine)
        if not spec.get("supported", True):
            manifest[engine] = {"supported": False, "error": spec["error"], "weights": []}
            continue
        env = resolve_path(cfg, spec["env"])
        extra_env = _engine_extra_env(cfg, engine)
        manifest[engine] = {
            "env": str(env) if env else None,
            "imports": _probe_python_env(
                env,
                spec["imports"],
                require_torch_kernel=bool(spec.get("requires_torch_kernel")),
                extra_env=extra_env,
            ),
            "binaries": [_probe_binary(env, binary, extra_env=extra_env) for binary in spec.get("binaries", [])],
            "weights": _engine_weight_manifest(cfg, engine, env),
        }
    return _jsonable({
        "project_root": str(PROJECT_ROOT),
        "run_mode": mode,
        "specificity_enabled": bool(cfg_get(cfg, "specificity.enabled", False)),
        "engines": manifest,
    })


def _mode_engines(cfg: dict, mode: str) -> list[str]:
    if mode == "complex":
        from .complex import validate_settings
        options = validate_settings(cfg_get(cfg, "complex", {}))
        return options.get("comparison_engines", [options.get("engine", "boltz2")])
    engines: list[str] = []

    provider = str(cfg_get(cfg, "structure.provider", "none")).lower()
    target_pdb = resolve_path(cfg, cfg_get(cfg, "target.pdb"))
    needs_structure_provider = provider not in {"none", "sequence", "mock"} and not (target_pdb and target_pdb.exists())
    if bool(cfg_get(cfg, "specificity.enabled", False)):
        cleaved_pdb = resolve_path(cfg, cfg_get(cfg, "specificity.cleaved_pdb"))
        needs_structure_provider = (mode == "design" and provider not in {"none", "sequence", "mock"} and
                                    (not (target_pdb and target_pdb.exists()) or not (cleaved_pdb and cleaved_pdb.exists())))
    if needs_structure_provider:
        engines.append("boltz2" if provider in {"boltz", "boltz2"} else provider)

    specificity_enabled = bool(cfg_get(cfg, "specificity.enabled", False))
    if specificity_enabled:
        engine = str(cfg_get(cfg, "specificity.engine", "boltz2")).lower()
        engines.append("boltz2" if engine in {"boltz", "boltz2"} else engine)

    if mode == "design":
        design_engine = str(cfg_get(cfg, "design.engine", "rfantibody")).lower()
        engines.extend(["rfantibody", "boltzgen"] if design_engine == "both" else [design_engine])
        if bool(cfg_get(cfg, "design.refinement.enabled", False)):
            engines.append("colabfold")
    elif mode == "screen":
        screen_engine = str(cfg_get(cfg, "screen.engine", "chai1")).lower()
        if not specificity_enabled:
            if cfg_get(cfg, "screen.comparison.enabled", False):
                from .engine_comparison import settings
                engines.extend(settings(cfg)["engines"])
            else:
                engines.append("boltz2" if screen_engine in {"boltz", "boltz2"} else screen_engine)
        if not specificity_enabled and int(cfg_get(cfg, "screen.boltz2_rescore_top", 0) or 0) > 0 and "boltz2" not in engines:
            engines.append("boltz2")
    elif mode == "mature":
        if str(cfg_get(cfg, "mature.cdr.source", "auto")).lower() == "auto":
            engines.append("anarci")
        if str(cfg_get(cfg, "mature.prefilter.mode", "esm2")).lower() == "esm2":
            engines.append("esm")
        if (str(cfg_get(cfg, "mature.prefilter.mode", "esm2")).lower() == "esm2"
                and bool(cfg_get(cfg, "mature.antifold.enabled", True))):
            engines.append("antifold")
        if not specificity_enabled:
            screen_engine = str(cfg_get(cfg, "screen.engine", "chai1")).lower()
            if cfg_get(cfg, "screen.comparison.enabled", False):
                from .engine_comparison import settings
                engines.extend(settings(cfg)["engines"])
            else:
                engines.append("boltz2" if screen_engine in {"boltz", "boltz2"} else screen_engine)

    cdr_source = str(cfg_get(cfg, "mature.cdr.source", "auto")).lower()
    if bool(cfg_get(cfg, "screen.cascade.enabled", False)) and "boltz2" not in engines:
        engines.append("boltz2")
    if mode in {"design", "screen"} and cdr_source == "auto":
        engines.append("anarci")

    ordered: list[str] = []
    for engine in engines:
        if engine not in ordered:
            ordered.append(engine)
    return ordered


def _followup_engines(cfg: dict, mode: str) -> list[str]:
    """모드가 직접 구동하지는 않지만 같은 실행에서 반드시 사용하는 엔진.

    design 모드는 후보 생성 뒤 tier2 에서 screen 엔진(+선택 Boltz-2 재평가)으로
    복합체를 예측한다(cli._tier2_and_finalize). 준비 상태를 점검하지 않으면
    doctor 가 통과한 뒤 tier2 에서 실패한다(R9-2).
    """
    if mode != "design" or bool(cfg_get(cfg, "specificity.enabled", False)):
        return []
    if cfg_get(cfg, "screen.comparison.enabled", False):
        from .engine_comparison import settings
        return settings(cfg)["engines"]
    engine = str(cfg_get(cfg, "screen.engine", "chai1")).lower()
    engines = ["boltz2" if engine in {"boltz", "boltz2"} else engine]
    if int(cfg_get(cfg, "screen.boltz2_rescore_top", 0) or 0) > 0 and "boltz2" not in engines:
        engines.append("boltz2")
    return engines


def _probe_engine(cfg: dict, engine: str, gpu: dict[str, Any], *, environment_only: bool = False) -> dict[str, Any]:
    spec = _engine_spec(cfg, engine)
    checks: list[dict[str, Any]] = []
    if not spec.get("supported", True):
        evidence = {"engine": engine, "blocker": spec["error"]}
        checks.append(_check(f"{engine}.supported", False, True, "engine_spec", evidence))
        return {"env": None, "supported": False, "checks": checks, "ok": False, "blocker": spec["error"]}

    env = resolve_path(cfg, spec["env"])
    report: dict[str, Any] = {"env": str(env) if env else None, "checks": checks}
    extra_env = _engine_extra_env(cfg, engine)

    env_probe = _probe_python_env(
        env,
        spec["imports"],
        require_torch_kernel=bool(spec.get("requires_torch_kernel")),
        extra_env=extra_env,
    )
    report["env_probe"] = env_probe
    checks.append(_check(f"{engine}.env_imports", bool(env_probe.get("ok")), True, env_probe.get("stage", "import"), env_probe))
    if spec.get("requires_jax_kernel"):
        jax_probe = _probe_jax(env, extra_env)
        report["jax_cuda"] = jax_probe
        checks.append(_check(f"{engine}.jax_cuda", bool(jax_probe.get("ok")), True, "jax_cuda_kernel", jax_probe))
    if spec.get("requires_headless_plot"):
        plot_probe = _probe_headless_plot(env, extra_env)
        report["headless_plot"] = plot_probe
        checks.append(_check(f"{engine}.headless_plot", bool(plot_probe.get("ok")), True, "headless_png", plot_probe))

    for binary in spec.get("binaries", []):
        binary_probe = _probe_binary(env, binary, extra_env=extra_env)
        checks.append(_check(f"{engine}.binary.{binary}", bool(binary_probe.get("ok")), True, binary_probe["stage"], binary_probe))

    if spec.get("requires_weights"):
        weights = _engine_weights(cfg, engine, env)
        report["weights"] = weights
        checks.append(_check(f"{engine}.weights", bool(weights.get("ok")), True, "weights", weights))

    if engine == "colabfold" and not environment_only:
        msa = _colabfold_msa_check(cfg)
        report["msa"] = msa
        checks.append(_check("colabfold.msa", msa["ok"], True, msa["stage"], msa))
    if engine == "opendde":
        from .opendde import installation_identity
        identity = installation_identity(env) if env else {"ok": False, "error": "missing environment"}
        report["installation_identity"] = identity
        checks.append(_check("opendde.installation_identity", identity["ok"], True, "pinned_source", identity))

    if engine == "antifold" and not environment_only:
        structure = _path_check(resolve_path(cfg, cfg_get(cfg, "mature.wt_structure")))
        if not structure["ok"]:
            structure["blocker"] = "AntiFold 활성화에는 mature.wt_structure가 필요합니다. 구조를 지정하거나 AntiFold를 끄세요."
        else:
            try:
                from .mature import resolve_wt_sequence, validate_antifold_structure
                chain, positions, _ = validate_antifold_structure(
                    resolve_path(cfg, cfg_get(cfg, "mature.wt_structure")), resolve_wt_sequence(cfg),
                    cfg_get(cfg, "mature.antifold.antigen_chain"), cfg_get(cfg, "mature.antifold.nanobody_chain"))
                structure.update(chain=chain, mapped_positions=len(positions))
            except Exception as exc:  # Input parsers also raise format-specific exceptions.
                structure.update(ok=False, blocker=str(exc))
        report["wt_structure"] = structure
        checks.append(_check("antifold.wt_structure", structure["ok"], True, "input", structure))

    if engine == "rfantibody":
        graph = _probe_rfantibody_graph(env, extra_env) if env_probe.get("ok") else {
            "ok": False, "stage": "readiness_gate", "blocker": "env/CUDA probe failed",
        }
        report["graph_cuda"] = graph
        checks.append(_check("rfantibody.graph_cuda", bool(graph.get("ok")), True, "dgl_cuda_ops", graph))
        framework = _path_check(resolve_path(cfg, cfg_get(cfg, "design.framework")))
        compat = _rfantibody_blackwell_compatibility(gpu, env_probe)
        report["framework"] = framework
        report["blackwell_compatibility"] = compat
        checks.append(_check("rfantibody.framework", bool(framework.get("ok")), True, "framework", framework))
        checks.append(_check("rfantibody.blackwell", bool(compat.get("ok")), True, compat["stage"], compat))
        versions = env_probe.get("imports", {})
        local_profile = (str(versions.get("torch", {}).get("version", "")).startswith("2.8.0") and
                         str(versions.get("dgl", {}).get("version", "")).startswith("1.1.3"))
        report["compatibility_profile"] = {
            "name": "blackwell_local" if local_profile else "other",
            "metadata_override_expected": local_profile,
            "upstream_metadata": "RFantibody pins torch 2.3 and cuda-python 11.8; local Blackwell profile overrides these pins.",
            "support_scope": "Runtime checks certify only the selected CUDA device, not all GPUs or scientific performance.",
        }
    if engine == "boltzgen":
        scaffold = _path_check(resolve_path(cfg, cfg_get(cfg, "design.boltzgen.scaffold") or cfg_get(cfg, "design.framework")))
        report["scaffold"] = scaffold
        checks.append(_check("boltzgen.scaffold", bool(scaffold.get("ok")), True, "scaffold", scaffold))

    report["ok"] = all(check.get("ok") for check in checks if check.get("required", False))
    return report


def _colabfold_msa_check(cfg):
    """Read-only MSA readiness. Exact generated-parent validation is run-local."""
    from .colabfold import settings
    from .colabfold_msa import from_config, cached_monomer, read_monomer, read_multimer
    from .colabfold_msa_local import LocalMSATransientError
    try:
        options = settings(cfg)
        if cfg_get(cfg, "run.mock", False) or cfg_get(cfg, "run.dry_run", False):
            return {"ok": True, "stage": "mock_msa_bypass", "actual_homologs_evaluated": False,
                    "query_validation_pending": True}
        if options["msa_mode"] != "precomputed":
            raise ValueError("Real ColabFold requires precomputed MSA; legacy single_sequence is read/mock only")
        if options["msa_dir"]:
            collection = from_config(cfg)
            expected = cfg_get(cfg, "screen.colabfold.msa_manifest_sha256")
            if expected and expected != collection.manifest_sha256:
                raise ValueError("Precomputed MSA manifest hash differs from frozen run settings")
            for entry in collection.manifest.get("queries", {}).values():
                if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str) or not isinstance(entry.get("chains"), dict):
                    raise ValueError("Per-query MSA entry lacks chain/hash metadata")
                read_multimer(collection._path(entry), entry["chains"], expected_sha256=entry["sha256"],
                              max_sequences=options["msa_max_sequences"])
            return {"ok": True, "stage": "precomputed_msa", "manifest_sha256": collection.manifest_sha256,
                    "static_chains": len(collection.manifest["static_chains"]),
                    "parents": len(collection.manifest["parents"]), "query_validation_pending": True,
                    "pairing": "unpaired", "network_requests": 0}
        if cfg_get(cfg, "screen.colabfold.msa_source") == "public_api":
            if cfg_get(cfg, "screen.allow_public_msa_server", False) is not True:
                raise ValueError("public_api requires explicit target/generated sequence upload consent")
            return {"ok": True, "stage": "authorized_public_api_msa", "query_validation_pending": True,
                    "prepared_query_inputs": False, "network_requests": 0,
                    "endpoint": "https://api.colabfold.com", "explicit_sequence_upload_authorized": True,
                    "search_deferred": True,
                    "note": "Preflight performs no network request. Exact target/generated queries are validated and uploaded before prediction."}
        if (cfg_get(cfg, "screen.comparison.enabled", False) or cfg_get(cfg, "complex.comparison_engines")) and cfg_get(cfg, "screen.colabfold.msa_source") == "local_mmseqs2":
            from .colabfold_msa_local import SSHTransport, DEFAULT_MIN_FREE_BYTES
            readiness = SSHTransport().call("probe", threads=8, min_free_bytes=DEFAULT_MIN_FREE_BYTES)
            status = readiness.get("status")
            identity = readiness.get("identity")
            verified = (isinstance(identity, dict) and
                        re.fullmatch("[a-f0-9]{64}", str(identity.get("database_manifest_sha256", ""))) is not None)
            installed = verified and status in {"ready", "busy", "resource_wait"}
            # The acquisition loop already persists resource waits, checks
            # cancellation/budget, and validates exact A3Ms before inference.
            # A busy verified provider must reach that loop, not fail as an
            # absent installation during preflight.
            return {"ok": installed, "stage": "registered_local_msa_provider",
                    "readiness": readiness, "query_validation_pending": True, "prepared_query_inputs": False,
                    "search_ready": installed and status == "ready",
                    "search_deferred": installed and status != "ready",
                    "public_api_requests": 0,
                    "note": "Reviewed target and verified generated VHH sequences are searched locally before prediction.",
                    "blocker": None if installed else "로컬 MSA 서버의 설치·DB·연결 검증에 실패했습니다."}
        cache = resolve_path(cfg, cfg_get(cfg, "screen.colabfold.msa_cache_dir", "msa/monomer_cache"))
        receipts = sorted(cache.glob("*/receipt.json")) if cache and cache.is_dir() else []
        if not receipts or len(receipts) > 10000:
            raise ValueError("Verified monomer MSA cache is missing/empty; prepare approved public/local MSAs first")
        entries = []
        for receipt in receipts:
            alignment = read_monomer(receipt.parent / "query.a3m", max_sequences=10000)
            if receipt.parent.name != hashlib.sha256(alignment.sequence.encode()).hexdigest():
                raise ValueError("Monomer MSA cache directory differs from its query sequence hash")
            entry = cached_monomer(cache, alignment.sequence)
            entries.append({"sequence_sha256": entry["receipt"]["sequence_sha256"],
                            "a3m_sha256": entry["sha256"], "homolog_count": entry["receipt"]["homolog_count"]})
        return {"ok": True, "stage": "verified_monomer_cache", "cache_entries": entries,
                "query_validation_pending": True, "prepared_query_inputs": False, "network_requests": 0,
                "note": "Exact target/parent/query checks run before inference; generated parents are projected locally after design."}
    except (ValueError, OSError, TypeError, AttributeError, LocalMSATransientError) as exc:
        return {"ok": False, "stage": "precomputed_msa", "network_requests": 0, "blocker": str(exc)}


def _probe_followup_engine(cfg: dict, engine: str, gpu: dict | None = None, *, environment_only: bool = False) -> dict[str, Any]:
    """Follow-up inference uses the same full readiness gate as primary engines."""
    report = _probe_engine(cfg, engine, gpu or {}, environment_only=True) if environment_only else _probe_engine(cfg, engine, gpu or {})
    spec = _engine_spec(cfg, engine)
    if not spec.get("supported", True):
        evidence = {"engine": engine, "role": "followup", "blocker": spec["error"]}
        return {"env": None, "supported": False, "ok": False, "role": "followup",
                "checks": [_check(f"{engine}.followup_ready", False, True, "engine_spec", evidence)]}
    env = resolve_path(cfg, spec["env"])
    python = _env_python(env)
    weights = _engine_weights(cfg, engine, env) if spec.get("requires_weights") else {"ok": True, "stage": "weights"}
    ok = python is not None and bool(weights.get("ok")) and bool(report.get("ok"))
    evidence: dict[str, Any] = {
        "engine": engine,
        "role": "followup",
        "env": str(env) if env else None,
        "python": str(python) if python else None,
        "weights": weights,
        "scope": "design tier2: import, CUDA kernel, 실행 파일과 로컬 가중치 점검.",
    }
    if not ok:
        evidence["blocker"] = (
            f"{engine} 환경 또는 로컬 가중치가 없습니다. design 모드는 후보 생성 뒤 tier2 에서 "
            f"이 엔진으로 복합체를 예측하므로 설계 이후 단계에서 실패합니다 "
            f"(envs/setup_{engine}.sh 설치 또는 screen.engine 변경)."
        )
    return {**report, "ok": ok, "role": "followup", "weights": weights,
            "checks": report["checks"] + [_check(f"{engine}.followup_ready", ok, True, "followup_readiness", evidence)]}


def _engine_spec(cfg: dict, engine: str) -> dict[str, Any]:
    if engine == "opendde":
        return {"supported": True, "env": cfg_get(cfg, "screen.opendde.env", "envs/opendde"),
                "imports": ["torch", "opendde", "runner.batch_inference"], "binaries": ["opendde"],
                "requires_torch_kernel": True, "requires_weights": True}
    if engine == "boltzgen":
        return {"supported": True, "env": cfg_get(cfg, "design.boltzgen.env", "envs/boltzgen"),
                "imports": ["torch", "boltzgen", "boltzgen.cli.boltzgen", "gemmi", "biotite"], "binaries": ["boltzgen"],
                "requires_torch_kernel": True, "requires_headless_plot": True, "requires_weights": True}
    if engine == "colabfold":
        return {"supported": True, "env": cfg_get(cfg, "screen.colabfold.env", "envs/colabfold"),
                "imports": ["colabfold", "alphafold", "jax", "haiku"], "binaries": ["colabfold_batch"],
                "requires_torch_kernel": False, "requires_jax_kernel": True, "requires_headless_plot": True, "requires_weights": True}
    if engine == "rfantibody":
        return {
            "supported": True,
            "env": cfg_get(cfg, "design.env"),
            "imports": ["torch", "dgl"],
            "binaries": ["rfdiffusion", "proteinmpnn", "rf2", "qvscorefile", "qvextractspecific"],
            "requires_torch_kernel": True,
            "requires_weights": True,
        }
    if engine == "boltz2":
        return {
            "supported": True,
            "env": cfg_get(cfg, "screen.boltz2_env", cfg_get(cfg, "structure.boltz2.env")),
            "imports": ["torch", "boltz"],
            "binaries": ["boltz"],
            "requires_torch_kernel": True,
            "requires_weights": True,
        }
    if engine == "chai1":
        return {
            "supported": True,
            "env": cfg_get(cfg, "screen.env", cfg_get(cfg, "structure.chai1.env")),
            "imports": ["torch", "chai_lab", "pyarrow", "pandera"],
            "binaries": ["chai-lab"],
            "requires_torch_kernel": True,
            "requires_headless_plot": True,
            "requires_weights": True,
        }
    if engine == "esm":
        return {
            "supported": True,
            "env": cfg_get(cfg, "mature.esm.env"),
            "imports": ["torch", "esm"],
            "binaries": [],
            "requires_torch_kernel": True,
            "requires_weights": True,
        }
    if engine == "antifold":
        return {
            "supported": True,
            "env": cfg_get(cfg, "mature.antifold.env", cfg_get(cfg, "mature.esm.env")),
            "imports": ["torch", "antifold", "antifold.main"],
            "binaries": [],
            "requires_torch_kernel": True,
            "requires_weights": True,
        }
    if engine == "anarci":
        return {
            "supported": True,
            "env": cfg_get(cfg, "mature.anarci.env"),
            "imports": ["anarci"],
            "binaries": ["hmmscan"],
            "requires_torch_kernel": False,
            "requires_weights": True,
        }
    return {"supported": False, "error": f"unsupported engine: {engine}", "env": None, "imports": [], "binaries": []}


def _engine_extra_env(cfg: dict, engine: str) -> dict[str, str]:
    env_vars: dict[str, Any] = {}
    if engine == "opendde":
        env_vars["OPENDDE_ROOT_DIR"] = str(resolve_path(cfg, cfg_get(cfg, "screen.opendde.data_dir", "models/opendde")))
    if engine == "boltzgen":
        env_vars.update(cfg_get(cfg, "design.boltzgen.env_vars", {}) or {})
        env_vars.update({"HF_HUB_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1"})
    if engine == "colabfold":
        # Upstream defaults oversubscribe GPU memory. Keep this worker bounded.
        env_vars.update({"XLA_PYTHON_CLIENT_PREALLOCATE": "false", "XLA_PYTHON_CLIENT_MEM_FRACTION": "0.7",
                         "TF_FORCE_UNIFIED_MEMORY": "0", "JAX_COMPILATION_CACHE_DIR": str(resolve_path(cfg, "models/colabfold/jax_cache"))})
        env_vars.update(cfg_get(cfg, "screen.colabfold.env_vars", {}) or {})
    if engine == "rfantibody":
        env_vars.update(cfg_get(cfg, "design.env_vars", {}) or {})
    if engine in {"boltz2", "chai1"}:
        env_vars.update(cfg_get(cfg, "screen.env_vars", {}) or {})
    if engine == "boltz2":
        env_vars.update(cfg_get(cfg, "structure.boltz2.env_vars", {}) or {})
    if engine == "chai1":
        env_vars.update(cfg_get(cfg, "structure.chai1.env_vars", {}) or {})
    if engine in {"esm", "antifold"}:
        env_vars.update(cfg_get(cfg, "mature.esm.env_vars", {}) or {})
    if engine == "antifold":
        env_vars.update(cfg_get(cfg, "mature.antifold.env_vars", {}) or {})
    return {str(key): str(value) for key, value in env_vars.items() if value is not None}


def _probe_headless_plot(env, extra_env):
    """Render a PNG in an engine env, without a display, network or user cache."""
    python = _env_python(env)
    if python is None:
        return {"ok": False, "blocker": "그래프를 점검할 Python 환경이 없습니다."}
    code = """import io,json,socket
def denied(*args, **kwargs):
    raise RuntimeError('network disabled during headless plot readiness')
socket.socket.connect = denied
socket.create_connection = denied
import matplotlib
matplotlib.use('Agg', force=True)
import matplotlib.pyplot as plt
figure, axes = plt.subplots()
try:
    axes.plot([0, 1], [0, 1])
    stream = io.BytesIO()
    figure.savefig(stream, format='png')
    png = stream.getvalue()
    assert png.startswith(bytes([137,80,78,71,13,10,26,10])) and len(png) > 100
    print(json.dumps({'ok':True,'matplotlib':matplotlib.__version__,
                      'backend':matplotlib.get_backend(),'png_bytes':len(png),'network_disabled':True}))
finally:
    plt.close(figure)
"""
    with tempfile.TemporaryDirectory(prefix="nanocofold-plot-") as cache:
        environment = _env_process_env(env, extra_env)
        environment.update(MPLCONFIGDIR=cache, MPLBACKEND="Agg")
        environment.pop("DISPLAY", None)
        environment.pop("WAYLAND_DISPLAY", None)
        process = _run([str(python), "-c", code], timeout=READINESS_TIMEOUT_SECONDS, env=environment)
    payload = _loads(process.get("stdout", ""))
    return {**payload, "ok": process["returncode"] == 0 and bool(payload.get("ok")), "process": process}


def _probe_jax(env, extra_env, *, offline=False):
    python = _env_python(env)
    if python is None:
        return {"ok": False, "blocker": "ColabFold python 환경이 없습니다."}
    prefix = """import socket,importlib
def denied(*args, **kwargs):
    raise RuntimeError('network disabled during offline readiness')
socket.socket.connect = denied
socket.create_connection = denied
for name in ('colabfold', 'alphafold', 'haiku'):
    importlib.import_module(name)
""" if offline else ""
    code = prefix + """import json,jax,jax.numpy as jnp
devices=jax.devices('gpu')
x=jax.device_put(jnp.ones((8,8)), devices[0])
y=(x@x).block_until_ready()
assert bool(jnp.isfinite(y).all()), 'JAX CUDA kernel returned non-finite values'
print(json.dumps({'ok':True,'jax':jax.__version__,'device':str(devices[0]),'network_disabled':OFFLINE}))
""".replace("OFFLINE", repr(offline))
    process = _run([str(python), "-c", code], timeout=READINESS_TIMEOUT_SECONDS, env=_env_process_env(env, extra_env))
    return {**_loads(process.get("stdout", "")), "ok": process["returncode"] == 0, "process": process}


def _probe_rfantibody_graph(env: Path | None, extra_env: dict[str, str]) -> dict[str, Any]:
    python = _env_python(env)
    if python is None:
        return {"ok": False, "blocker": "RFantibody python not found"}
    script = Path(__file__).parent / "scripts" / "check_rfantibody_runtime.py"
    # This includes first-use library loading and PTX compilation.
    proc = _run([str(python), str(script)], timeout=READINESS_TIMEOUT_SECONDS, env=_env_process_env(env, extra_env))
    payload = _loads(proc.get("stdout", ""))
    return {**payload, "ok": proc["returncode"] == 0 and bool(payload.get("ok")), "process": proc}


def _probe_gpu() -> dict[str, Any]:
    nvidia = shutil.which("nvidia-smi")
    if not nvidia:
        return {"available": False, "stage": "nvidia-smi", "reason": "nvidia-smi not found"}
    proc = _run([nvidia, "--query-gpu=name,memory.total,driver_version,compute_cap", "--format=csv,noheader"], timeout=10)
    result = {"available": proc["returncode"] == 0, "stage": "nvidia-smi", **proc}
    if proc["returncode"] == 0 and proc.get("stdout"):
        rows = []
        for line in proc["stdout"].splitlines():
            parts = [part.strip() for part in line.split(",")]
            rows.append({
                "name": parts[0] if len(parts) > 0 else "",
                "memory_total": parts[1] if len(parts) > 1 else "",
                "driver_version": parts[2] if len(parts) > 2 else "",
                "compute_capability": parts[3] if len(parts) > 3 else "",
            })
        result["devices"] = rows
    return result


def _probe_python_env(
    env: Path | None,
    imports: list[str],
    require_torch_kernel: bool = False,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    python = _env_python(env)
    if python is None:
        return {"ok": False, "stage": "env_python", "env": str(env) if env else None, "blocker": "python executable not found"}
    if not imports:
        return {"ok": False, "stage": "import_spec", "python": str(python), "blocker": "no imports specified"}
    code = r"""
import importlib, json
mods = {}
for name in IMPORTS:
    try:
        module = importlib.import_module(name)
        mods[name] = {"ok": True, "version": str(getattr(module, "__version__", "unknown"))}
    except Exception as exc:
        mods[name] = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
torch_info = {"present": False}
try:
    import torch
    torch_info = {
        "present": True,
        "version": str(torch.__version__),
        "cuda_version": str(torch.version.cuda),
        "cuda_available": bool(torch.cuda.is_available()),
        "kernel_ok": False,
    }
    if torch.cuda.is_available():
        x = torch.randn((8, 8), device="cuda")
        y = x @ x
        torch.cuda.synchronize()
        torch_info["kernel_ok"] = bool(y.is_cuda and y.numel() == 64)
        torch_info["device_name"] = torch.cuda.get_device_name(0)
        torch_info["capability"] = ".".join(str(v) for v in torch.cuda.get_device_capability(0))
except Exception as exc:
    torch_info["error"] = f"{exc.__class__.__name__}: {exc}"
print(json.dumps({"imports": mods, "torch": torch_info}, sort_keys=True))
""".replace("IMPORTS", repr(imports))
    proc = _run([str(python), "-c", code], timeout=READINESS_TIMEOUT_SECONDS, env=_env_process_env(env, extra_env))
    payload = _loads(proc.get("stdout", ""))
    import_results = payload.get("imports", {})
    missing = [name for name in imports if not import_results.get(name, {}).get("ok")]
    torch_info = payload.get("torch", {})
    torch_ok = True
    if require_torch_kernel:
        torch_ok = bool(torch_info.get("present") and torch_info.get("cuda_available") and torch_info.get("kernel_ok"))
    ok = proc["returncode"] == 0 and not missing and torch_ok
    result = {"ok": ok, "stage": "import_cuda_kernel", "python": str(python), "process": proc, **payload}
    if missing:
        result["missing_imports"] = missing
    if require_torch_kernel and not torch_ok:
        result["blocker"] = "torch CUDA kernel probe failed or CUDA is unavailable"
    return result


def _probe_binary(env: Path | None, name: str, extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    executable = _env_binary(env, name)
    if executable is None:
        return {"ok": False, "stage": "binary_lookup", "name": name, "blocker": "executable not found"}
    proc = _run([str(executable), "-h" if name == "hmmscan" else "--help"], timeout=READINESS_TIMEOUT_SECONDS, env=_env_process_env(env, extra_env))
    return {
        "ok": proc["returncode"] == 0,
        "stage": "help",
        "name": name,
        "path": str(executable),
        "version_or_help": (proc.get("stdout") or "").splitlines()[:3],
        "process": proc,
    }


_RFANTIBODY_REQUIRED_WEIGHTS = {
    "RFdiffusion_Ab.pt": [
        PROJECT_ROOT / "tools" / "RFantibody" / "weights" / "RFdiffusion_Ab.pt",
        PROJECT_ROOT / "tools" / "RFantibody" / "include" / "weights" / "RFdiffusion_Ab.pt",
    ],
    "RF2_ab.pt": [PROJECT_ROOT / "tools" / "RFantibody" / "weights" / "RF2_ab.pt"],
    "ProteinMPNN_v48_noise_0.2.pt": [
        PROJECT_ROOT / "tools" / "RFantibody" / "weights" / "ProteinMPNN_v48_noise_0.2.pt"
    ],
}


_BOLTZ2_REQUIRED_FILES = ("boltz2_conf.ckpt", "boltz2_aff.ckpt")
_CHAI1_REQUIRED_FILES = (
    "feature_embedding.pt",
    "bond_loss_input_proj.pt",
    "token_embedder.pt",
    "trunk.pt",
    "diffusion_module.pt",
    "confidence_head.pt",
)


def _engine_weights(cfg: dict, engine: str, env: Path | None) -> dict[str, Any]:
    if engine == "opendde":
        from .opendde import readiness
        return readiness(cfg)
    if engine == "boltzgen":
        from .design_boltzgen import model_paths
        assets = json.loads((Path(__file__).parent / "_data/model-assets.json").read_text())["assets"]
        manifest = _engine_weight_manifest(cfg, engine, env)
        found = {Path(row["path"]).name: row for row in manifest}
        required = list(model_paths(cfg))
        expected = {value["relative_path"]: value["sha256"] for value in assets.values() if value.get("engine") == "boltzgen"}
        missing = [name for name in required if name not in found]
        bad = [name for name in required if name in found and found[name]["sha256"] != expected.get(name)]
        return {"ok": not missing and not bad, "required": required, "found": manifest, "missing": missing,
                "hash_mismatch": bad, "stage": "weights", "blocker": "BoltzGen pinned models missing or mismatched; setup --profile boltzgen" if missing or bad else None}
    if engine == "colabfold":
        required = [f"params_model_{i}_multimer_v3.npz" for i in range(1, 6)]
        manifest = _engine_weight_manifest(cfg, engine, env)
        byname = {Path(row["path"]).name: row for row in manifest}
        receipt = resolve_path(cfg, cfg_get(cfg, "screen.colabfold.data_dir", "models/colabfold")) / "weights.json"
        try:
            recorded = json.loads(receipt.read_text())["files"]
        except (OSError, ValueError, KeyError):
            recorded = {}
        missing = [name for name in required if name not in byname]
        bad = [name for name in required if name in byname and recorded.get(name) != byname[name]["sha256"]]
        return {"ok": not missing and not bad, "required": required, "found": manifest, "missing": missing,
                "hash_mismatch": bad, "stage": "weights", "blocker": "ColabFold 가중치 누락 또는 검증 명세 불일치. setup --profile colabfold를 실행하세요." if missing or bad else None}
    if engine == "rfantibody":
        return _rfantibody_weights(cfg)
    if engine == "boltz2":
        return _boltz2_weights(cfg)
    if engine == "chai1":
        return _chai1_weights(cfg, env)
    if engine == "anarci":
        required = ["ALL.hmm" + suffix for suffix in ("", ".h3f", ".h3i", ".h3m", ".h3p")]
        manifest = _engine_weight_manifest(cfg, engine, env)
        names = {Path(item["path"]).name for item in manifest}
        missing = [name for name in required if name not in names]
        return {"ok": not missing, "stage": "weights", "required": required, "found": manifest,
                "missing": missing, "blocker": f"missing ANARCI HMM database: {', '.join(missing)}" if missing else None}
    if engine == "esm":
        manifest = _engine_weight_manifest(cfg, engine, env)
        names = {Path(item["path"]).name for item in manifest}
        model = str(cfg_get(cfg, "mature.esm.model", "esm2_t33_650M_UR50D"))
        required = [f"{model}.pt"]
        if not any(part in model for part in ("esm1v", "esm_if", "270K")):
            required.append(f"{model}-contact-regression.pt")
        missing = [name for name in required if name not in names]
        return {"ok": not missing, "stage": "weights", "required": required, "found": manifest,
                "missing": missing, "blocker": f"missing ESM files: {', '.join(missing)}" if missing else None}
    manifest = _engine_weight_manifest(cfg, engine, env)
    expected_hash = cfg_get(cfg, "mature.antifold.sha256") if engine == "antifold" else None
    hash_ok = not expected_hash or all(item["sha256"] == expected_hash for item in manifest)
    return {
        "ok": bool(manifest) and hash_ok,
        "stage": "weights",
        "required": "at_least_one_local_model_file",
        "found": manifest,
        "blocker": f"{engine} weight hash mismatch" if not hash_ok else None if manifest else f"{engine} local model/weight files were not discovered",
    }


def _rfantibody_weights(cfg: dict | None = None) -> dict[str, Any]:
    found: dict[str, dict[str, Any]] = {}
    searched: dict[str, list[str]] = {}
    manifest = Path(__file__).parent / "_data" / "rfantibody-weights.sha256"
    expected = {line.split()[1]: line.split()[0] for line in manifest.read_text().splitlines() if line.strip()}
    extra_env = _engine_extra_env(cfg or {}, "rfantibody")
    weight_dir = extra_env.get("RFANTIBODY_WEIGHTS") or os.environ.get("RFANTIBODY_WEIGHTS")
    source_root = extra_env.get("RFANTIBODY_ROOT") or os.environ.get("RFANTIBODY_ROOT")
    if not weight_dir and source_root:
        weight_dir = str(Path(source_root) / "weights")
    for name, candidates in _RFANTIBODY_REQUIRED_WEIGHTS.items():
        if weight_dir:
            candidates = [Path(weight_dir).expanduser().resolve() / name]
        searched[name] = [str(path) for path in candidates]
        hit = next((path for path in candidates if path.exists() and path.stat().st_size > 0), None)
        if hit:
            found[name] = _file_manifest(hit)
            found[name]["expected_sha256"] = expected.get(name)
            found[name]["hash_ok"] = found[name]["sha256"] == expected.get(name)
    missing = [name for name in _RFANTIBODY_REQUIRED_WEIGHTS if name not in found]
    mismatch = [name for name, info in found.items() if not info["hash_ok"]]
    return {
        "ok": not missing and not mismatch,
        "stage": "weights",
        "required": list(_RFANTIBODY_REQUIRED_WEIGHTS),
        "searched": searched,
        "found": found,
        "missing": missing,
        "hash_mismatch": mismatch,
        "blocker": (f"missing RFantibody weights: {', '.join(missing)}" if missing else
                    f"unverified RFantibody weight hashes: {', '.join(mismatch)}" if mismatch else None),
    }


def _boltz2_weights(cfg: dict) -> dict[str, Any]:
    cache = _boltz_cache_path(cfg)
    found = {}
    missing = []
    for name in _BOLTZ2_REQUIRED_FILES:
        path = cache / name
        if path.exists() and path.is_file() and path.stat().st_size > 0:
            found[name] = _file_manifest(path)
        else:
            missing.append(name)
    mols = cache / "mols"
    mol_files = list(mols.glob("*.pkl")) if mols.is_dir() else []
    support_ok = False
    support = {"path": str(mols), "exists": mols.is_dir(), "pkl_count": len(mol_files), "verified": False}
    try:
        from .install import _verify_boltz_molecules
        if not mols.is_dir() or mols.is_symlink() or not (cache / "mols.tar").is_file():
            raise ValueError("검증 가능한 mols.tar와 일반 mols 디렉터리가 필요합니다.")
        assets = json.loads((Path(__file__).parent / "_data/model-assets.json").read_text())["assets"]
        if _sha256(cache / "mols.tar") != assets["mols.tar"]["sha256"]:
            raise ValueError("Boltz 분자 사전 아카이브 해시 불일치")
        receipt_path = cache.parent.parent / ".platform/model-assets.json"
        trusted_index = None
        if receipt_path.is_file():
            receipt = json.loads(receipt_path.read_text())
            trusted_index = receipt.get("support_indexes", {}).get(str(cache / ".nanocofold-molecules.json"), {}).get("sha256")
        support["verified_count"] = _verify_boltz_molecules(cache, verified_index_sha=trusted_index, write_index=False)
        support["verified"] = support_ok = True
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
        support["error"] = str(exc)
    if not support_ok:
        missing.append("mols/*.pkl")
    return {
        "ok": not missing,
        "stage": "weights",
        "cache": str(cache),
        "required": list(_BOLTZ2_REQUIRED_FILES) + ["mols/*.pkl"],
        "found": found,
        "support": support,
        "missing": missing,
        "blocker": None if not missing else f"missing Boltz-2 cache assets: {', '.join(missing)}",
    }


def _chai1_weights(cfg: dict, env: Path | None) -> dict[str, Any]:
    downloads = _chai_downloads_path(cfg, env)
    model_dir = downloads / "models_v2"
    found = {}
    missing = []
    for name in _CHAI1_REQUIRED_FILES:
        path = model_dir / name
        if path.exists() and path.is_file() and path.stat().st_size > 0:
            found[name] = _file_manifest(path)
        else:
            missing.append(name)
    support_required = ("conformers_v1.apkl", "esm/traced_sdpa_esm2_t36_3B_UR50D_fp16.pt")
    for name in support_required:
        path = downloads / name
        if path.is_file() and path.stat().st_size > 0:
            found[name] = _file_manifest(path)
        else:
            missing.append(name)
    return {
        "ok": not missing,
        "stage": "weights",
        "downloads": str(downloads),
        "required": list(_CHAI1_REQUIRED_FILES) + list(support_required),
        "found": found,
        "missing": missing,
        "blocker": None if not missing else f"missing Chai-1 exported modules: {', '.join(missing)}",
    }


def _engine_weight_manifest(cfg: dict, engine: str, env: Path | None) -> list[dict[str, Any]]:
    if engine == "opendde":
        from .opendde import model_paths
        return [_file_manifest(path) for path in model_paths(cfg).values() if path.is_file()]
    if engine == "boltzgen":
        from .design_boltzgen import model_paths
        return [_file_manifest(path) for path in model_paths(cfg).values() if path.is_file()]
    if engine == "colabfold":
        root = resolve_path(cfg, cfg_get(cfg, "screen.colabfold.data_dir", "models/colabfold")) / "params"
        return [_file_manifest(path) for path in sorted(root.glob("params_model_*_multimer_v3.npz")) if path.is_file()]
    if engine == "rfantibody":
        paths = [Path(item["path"]) for item in _rfantibody_weights(cfg)["found"].values()]
    elif engine == "boltz2":
        paths = [Path(item["path"]) for item in _boltz2_weights(cfg)["found"].values()]
    elif engine == "chai1":
        paths = [Path(item["path"]) for item in _chai1_weights(cfg, env)["found"].values()]
    elif engine == "antifold":
        path = antifold_model_path(cfg, env)
        paths = [path] if path and path.is_file() and path.stat().st_size > 0 else []
    elif engine == "esm":
        paths = _torch_hub_checkpoints(cfg, engine)
    elif engine == "anarci":
        sites = _site_packages_roots(env) if env else [Path(p) for p in sys.path if "site-packages" in p]
        paths = [site / "anarci" / "dat" / "HMMs" / ("ALL.hmm" + suffix)
                 for site in sites for suffix in ("", ".h3f", ".h3i", ".h3m", ".h3p")]
        paths = [path for path in paths if path.is_file() and path.stat().st_size > 0]
    else:
        roots = [path for path in _candidate_weight_roots(cfg, engine, env) if path.exists()]
        patterns = ("*.pt", "*.pth", "*.ckpt", "*.safetensors", "*.npz")
        paths = []
        for root in roots:
            for pattern in patterns:
                paths.extend(root.rglob(pattern))
        paths.extend(_torch_hub_checkpoints(cfg, engine))
        paths = sorted(set(path for path in paths if path.is_file()))
    return [_file_manifest(path) for path in paths]


def antifold_model_path(cfg: dict, env: Path | None) -> Path | None:
    """Exact file used by the local wrapper or AntiFold 0.3.1's loader.

    Upstream uses site-packages/models/model.pt rather than antifold/models,
    and its --model_path argument is ignored. Never accept unrelated *.pt files.
    """
    explicit = cfg_get(cfg, "mature.antifold.model_path")
    if explicit:
        return resolve_path(cfg, explicit)
    candidates = [PROJECT_ROOT / "models" / "antifold" / "model.pt"]
    if env:
        candidates.extend(site / "models" / "model.pt" for site in _site_packages_roots(env))
    else:
        import importlib.util
        try:
            spec = importlib.util.find_spec("antifold")
        except (ImportError, ValueError):
            spec = None
        if spec and spec.origin:
            candidates.append(Path(spec.origin).parent.parent / "models" / "model.pt")
    return next((path for path in candidates if path.is_file() and path.stat().st_size > 0), None)


def _torch_hub_checkpoints(cfg: dict, engine: str) -> list[Path]:
    """fair-esm 은 torch.hub 캐시에 가중치를 내려받는다(프로젝트 밖이어도 로컬 가중치다).

    캐시 전체를 훑지 않고 설정된 모델 파일만 확인한다(무관한 대용량 체크포인트 해싱 방지).
    """
    if engine != "esm":
        return []
    env_vars = _engine_extra_env(cfg, engine)
    torch_home = env_vars.get("TORCH_HOME") or os.environ.get("TORCH_HOME")
    if torch_home:
        hub_root = Path(torch_home).expanduser()
    else:
        cache_root = env_vars.get("XDG_CACHE_HOME") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
        hub_root = Path(cache_root).expanduser() / "torch"
    model = str(cfg_get(cfg, "mature.esm.model", "esm2_t33_650M_UR50D"))
    candidates = [hub_root / "hub" / "checkpoints" / f"{model}.pt",
                  hub_root / "hub" / "checkpoints" / f"{model}-contact-regression.pt"]
    return [candidate for candidate in candidates if candidate.is_file() and candidate.stat().st_size > 0]


def _candidate_weight_roots(cfg: dict, engine: str, env: Path | None) -> list[Path]:
    package_names = {
        "boltz2": ["boltz"],
        "chai1": ["chai_lab"],
        "esm": ["esm"],
        "antifold": ["antifold"],
    }.get(engine, [engine])
    roots = [
        PROJECT_ROOT / "tools" / engine,
        PROJECT_ROOT / "data" / engine,
    ]
    if env:
        roots.extend([
            env / "models",
            env / "weights",
            env / "cache",
        ])
        for site_packages in _site_packages_roots(env):
            for package in package_names:
                roots.append(site_packages / package)
    return roots


def _site_packages_roots(env: Path) -> list[Path]:
    roots = []
    for lib in (env / "lib").glob("python*/site-packages"):
        if lib.is_dir():
            roots.append(lib)
    windows = env / "Lib" / "site-packages"
    if windows.is_dir():
        roots.append(windows)
    return roots


def _boltz_cache_path(cfg: dict) -> Path:
    extra_env = _engine_extra_env(cfg, "boltz2")
    value = extra_env.get("BOLTZ_CACHE") or os.environ.get("BOLTZ_CACHE")
    if value:
        return Path(value).expanduser().resolve()
    for args_key in ("score.boltz2_extra_args", "structure.boltz2.extra_args"):
        cache = _arg_value(cfg_get(cfg, args_key, []) or [], "--cache")
        if cache:
            return Path(cache).expanduser().resolve()
    return Path.home() / ".boltz"


def _chai_downloads_path(cfg: dict, env: Path | None) -> Path:
    extra_env = _engine_extra_env(cfg, "chai1")
    value = extra_env.get("CHAI_DOWNLOADS_DIR") or os.environ.get("CHAI_DOWNLOADS_DIR")
    if value:
        return Path(value).expanduser().resolve()
    for site_packages in _site_packages_roots(env) if env else []:
        repo_downloads = site_packages / "downloads"
        if repo_downloads.exists():
            return repo_downloads
    return PROJECT_ROOT / "envs" / "chai1" / "lib" / "python3.11" / "site-packages" / "downloads"


def _arg_value(args: list[Any], key: str) -> str | None:
    items = [str(item) for item in args]
    for index, item in enumerate(items):
        if item == key and index + 1 < len(items):
            return items[index + 1]
        if item.startswith(f"{key}="):
            return item.split("=", 1)[1]
    return None


def _file_manifest(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    from .provenance import file_digest
    return file_digest(path)


def _rfantibody_blackwell_compatibility(gpu: dict[str, Any], env_probe: dict[str, Any]) -> dict[str, Any]:
    caps = [str(device.get("compute_capability", "")) for device in gpu.get("devices", [])]
    blackwell = any(_cap_major(cap) >= 12 for cap in caps)
    if not blackwell:
        return {"ok": True, "stage": "blackwell_compatibility", "blackwell_gpu": False, "evidence": caps}

    torch_info = env_probe.get("torch", {})
    kernel_ok = bool(torch_info.get("kernel_ok"))
    active_capability = str(torch_info.get("capability") or "")
    active_blackwell = _cap_major(active_capability) >= 12
    cuda_version = str(torch_info.get("cuda_version") or "")
    cuda_ok = _cuda_at_least(cuda_version, 12, 8)
    ok = bool(kernel_ok and cuda_ok and active_blackwell)
    result = {
        "ok": ok,
        "stage": "blackwell_compatibility",
        "blackwell_gpu": True,
        "compute_capabilities": caps,
        "torch_cuda_version": cuda_version,
        "torch_kernel_ok": kernel_ok,
        "probed_capability": active_capability,
        "selected_blackwell_verified": bool(kernel_ok and cuda_ok and active_blackwell),
    }
    if not ok:
        result["blocker"] = (
            "RFantibody env did not prove Blackwell compatibility: expected torch CUDA >=12.8 "
            "and a successful kernel on the selected sm_120+ device. "
            "On mixed-GPU hosts, select the Blackwell device with design.env_vars.CUDA_VISIBLE_DEVICES."
        )
    return result



def _public_smoke_fixtures() -> tuple[Path, Path]:
    """Locate the registered distribution fixture without shipping private data."""
    from .runtime_policy import runtime_policy
    directory = runtime_policy()["smoke_fixture_directory"]
    if directory == "data/benchmark":
        root = PROJECT_ROOT / directory
        return root / "target.fasta", root / "binder_library.fasta"
    package = Path(__file__).resolve().parent
    roots = (package / "_data/examples/tutorial", package.parent / directory,
             PROJECT_ROOT / directory)
    for root in roots:
        paths = root / "target.fasta", root / "vhh_library.fasta"
        if all(path.is_file() for path in paths):
            return paths
    return roots[0] / "target.fasta", roots[0] / "vhh_library.fasta"


def _smoke(cfg: dict, mode: str, engines: list[str], reports: dict[str, Any]) -> dict[str, Any]:
    coverage = _initial_smoke_coverage(engines)
    if any(not reports[engine].get("ok") for engine in engines):
        return {
            "ok": False,
            "stage": "readiness_gate",
            "attempted": False,
            "blocker": "required readiness checks failed",
            "coverage": coverage,
        }
    results = {}
    with tempfile.TemporaryDirectory(prefix="nanocofold-doctor-") as tmp:
        for engine in engines:
            if not coverage[engine]["required"]:
                continue
            env = resolve_path(cfg, _engine_spec(cfg, engine)["env"])
            if not _engine_weight_manifest(cfg, engine, env):
                return {
                    "ok": False, "stage": "local_weights_gate", "attempted": bool(results),
                    "blocker": f"{engine} local weights not found; refusing auto-downloads.",
                    "coverage": coverage, "results": results,
                }
            work = Path(tmp) / engine
            work.mkdir()
            if engine == "rfantibody":
                result = _run_rfantibody_smoke(cfg, env, work)
            else:
                fixture, binder_fixture = _public_smoke_fixtures()
                if not fixture.exists() or not binder_fixture.exists():
                    result = {"ok": False, "stage": "fixture_lookup", "attempted": False,
                              "blocker": "examples/tutorial FASTA fixtures not found"}
                else:
                    result = _run_fixture_smoke(cfg, engine, env, fixture, binder_fixture, work)
            results[engine] = result
            coverage[engine].update({"verified": bool(result.get("ok")), "stage": result.get("stage"),
                                     "attempted": bool(result.get("attempted"))})
    unverified = [name for name, item in coverage.items() if item["required"] and not item["verified"]]
    # Keep single-engine evidence accessible in its previous location.
    report = dict(next(iter(results.values()))) if len(results) == 1 else {"stage": "multi_engine_smoke"}
    report.update({"ok": bool(results) and not unverified, "coverage": coverage, "results": results,
                   "attempted": any(item.get("attempted") for item in results.values())})
    if unverified or not results:
        report["blocker"] = "required smoke coverage missing: " + (", ".join(unverified) or mode)
    return report


def _initial_smoke_coverage(engines: list[str]) -> dict[str, dict[str, Any]]:
    coverage: dict[str, dict[str, Any]] = {}
    for engine in engines:
        if engine in {"boltz2", "chai1", "rfantibody"}:
            coverage[engine] = {"required": True, "verified": False, "attempted": False, "stage": "pending"}
        else:
            coverage[engine] = {"required": False, "verified": None, "attempted": False, "stage": "not_applicable"}
    return coverage


def _run_rfantibody_smoke(cfg: dict, env: Path | None, work: Path) -> dict[str, Any]:
    """One VHH through all three real models and extraction; not a quality test."""
    from . import design_rfantibody
    from .utils import shell

    root = Path(_engine_extra_env(cfg, "rfantibody").get("RFANTIBODY_ROOT") or
                os.environ.get("RFANTIBODY_ROOT") or PROJECT_ROOT / "tools" / "RFantibody")
    inputs = root / "test" / "rfdiffusion" / "inputs_for_test"
    target = inputs / "rsv_site3.pdb"
    framework = inputs / "h-NbBCII10.pdb"
    report = {"ok": False, "stage": "rfantibody_pipeline", "attempted": False, "engine": "rfantibody",
              "scope": "RSV fixture; 3 diffusion steps, 1 sequence, 1 RF2 recycle; runtime only"}
    if not target.is_file() or not framework.is_file():
        return {**report, "blocker": "RFantibody RSV/VHH test fixtures not found"}
    smoke_cfg = deepcopy(cfg)
    smoke_cfg["design"] = {
        **smoke_cfg.get("design", {}), "engine": "rfantibody", "env": str(env) if env else None,
        "framework": str(framework), "num_designs": 1, "chunk_size": 1,
        "loops": "H1:7,H2:6,H3:5", "deterministic": True,
        "extra_args": ["inference.final_step=48", "inference.write_trajectory=False"],
        "mpnn": {"seqs_per_struct": 1, "temperature": 0.2}, "rf2": {"num_recycles": 1},
        # Numerical output, not biological quality, is the acceptance criterion.
        "filters": {"rf2_pae": 1e9, "design_rmsd": 1e9, "top_n_to_score": 1},
    }
    report["attempted"] = True
    report["inputs"] = [_file_manifest(target), _file_manifest(framework)]
    start = time.monotonic()
    try:
        with shell.execution_budget(shell.remaining_budget(600)), redirect_stdout(StringIO()):
            result = design_rfantibody.run_design(
                smoke_cfg, work, {"structure": str(target), "hotspot_labels": ["T:305", "T:456"]},
            )
        report["artifacts"] = _rfantibody_smoke_artifacts(result)
        report["ok"] = report["artifacts"]["ok"]
    except Exception as exc:
        report["blocker"] = f"{type(exc).__name__}: {exc}"
    report["seconds"] = round(time.monotonic() - start, 3)
    report["logs"] = [_file_manifest(path) for path in sorted(work.rglob("*.log"))]
    return report


def _rfantibody_smoke_artifacts(result: dict) -> dict[str, Any]:
    import numpy as np
    from .utils import pdb_io

    candidates = result["candidates"]
    if len(candidates) != 1:
        return {"ok": False, "blocker": f"expected one candidate, found {len(candidates)}"}
    row = candidates.iloc[0]
    scores = {name: float(row[name]) for name in ("rf2_pae", "rf2_plddt", "rmsd")}
    structure_path = Path(row["pred_dir"]) if row.get("pred_dir") else None
    if not structure_path or not structure_path.is_file():
        return {"ok": False, "blocker": "extracted RF2 PDB missing"}
    structure = pdb_io.load_structure(structure_path)
    model = next(structure.get_models())
    chains = {chain.id: pdb_io.chain_sequence(chain) for chain in model}
    coords = np.asarray([atom.coord for atom in model.get_atoms()])
    files = [Path(path) for path in result["info"]["quivers"]] + [Path(result["info"]["score_file"])]
    files_ok = all(path.is_file() and path.stat().st_size > 0 for path in files)
    ok = (files_ok and np.isfinite(list(scores.values())).all() and bool(chains.get("T")) and
          bool(chains.get("H")) and chains["H"] == row["sequence"] and coords.size > 0 and
          np.isfinite(coords).all() and not result["info"].get("warnings"))
    return {"ok": bool(ok), "scores": scores, "chain_lengths": {key: len(seq) for key, seq in chains.items()},
            "finite_coordinates": bool(coords.size and np.isfinite(coords).all()),
            "structure": _file_manifest(structure_path),
            "files": [_file_manifest(path) for path in files if path.is_file()]}


def _run_fixture_smoke(cfg: dict, engine: str, env: Path | None, target_fasta: Path, binder_fasta: Path, work: Path) -> dict[str, Any]:
    target_sequence = _first_fasta_sequence(target_fasta)
    binder_sequence = _first_fasta_sequence(binder_fasta)
    target_sequence = target_sequence[:64]
    binder_sequence = binder_sequence[:64]
    extra_env = _engine_extra_env(cfg, engine)
    if engine == "boltz2":
        from .screen import write_boltz_yaml

        # 설정된 실행 경로와 동일하게 점검한다: CPU FP32 opt-in 이면 wrapper 를 거친다(R9-3).
        cpu_fp32 = bool(cfg_get(cfg, "score.boltz2_cpu_fp32", False))
        extra_args = [str(item) for item in (cfg_get(cfg, "score.boltz2_extra_args", []) or [])]
        if cpu_fp32:
            from .scripts.boltz_cpu_fp32 import validate_cpu_args

            python = _env_python(env)
            if python is None:
                return {"ok": False, "stage": "env_python", "attempted": False,
                        "blocker": "boltz2 python executable not found"}
            try:
                validate_cpu_args(["predict", *extra_args])
            except ValueError as exc:
                return {"ok": False, "stage": "cpu_fp32_args", "attempted": False, "blocker": str(exc)}
            command = [str(python), str(_SCRIPTS_DIR / "boltz_cpu_fp32.py")]
            # CPU 추론은 GPU보다 훨씬 느리므로 별도 상한을 사용한다(캠페인 예산이 있으면 그 값이 우선).
            timeout = 1800.0
        else:
            executable = _env_binary(env, "boltz")
            if executable is None:
                return {"ok": False, "stage": "binary_lookup", "attempted": False, "blocker": "boltz executable not found"}
            command = [str(executable)]
            timeout = 300.0
        yaml_path = write_boltz_yaml(
            [
                {"id": "A", "sequence": target_sequence, "msa": "empty"},
                {"id": "B", "sequence": binder_sequence, "msa": "empty"},
            ],
            work / "smoke_boltz.yaml",
        )
        argv = [
            *command, "predict", str(yaml_path),
            "--out_dir", str(work / "boltz_out"),
            "--override",
            "--output_format", "mmcif",
            "--recycling_steps", "1",
            "--diffusion_samples", "1",
            "--seed", str(_run_seed(cfg)),
            "--write_full_pae",
            "--subsample_msa",
            "--num_subsampled_msa", "1",
        ]
        argv.extend(extra_args)
        proc = _run(argv, timeout=timeout, env=_env_process_env(env, extra_env))
        artifacts = _smoke_artifacts(engine, work / "boltz_out", len(target_sequence) + len(binder_sequence))
        return {
            "ok": proc["returncode"] == 0 and bool(artifacts.get("ok")),
            "stage": "real_fixture_inference",
            "attempted": True,
            "engine": engine,
            "process": proc,
            "artifacts": artifacts,
        }

    python = _env_python(env)
    if python is None:
        return {"ok": False, "stage": "env_python", "attempted": False, "blocker": "chai1 python executable not found"}
    from .screen import write_chai_fasta

    fasta = work / "smoke_chai.fasta"
    write_chai_fasta({"A": target_sequence, "B": binder_sequence}, fasta)
    argv = [
        str(python),
        str(_SCRIPTS_DIR / "chai_fold.py"),
        "--fasta", str(fasta),
        "--out-dir", str(work / "chai_out"),
        "--seed", str(_run_seed(cfg)),
        "--num-trunk-recycles", "1",
        "--num-diffn-timesteps", "1",
        "--num-diffn-samples", "1",
    ]
    proc = _run(argv, timeout=300, env=_env_process_env(env, extra_env))
    artifacts = _smoke_artifacts(engine, work / "chai_out", len(target_sequence) + len(binder_sequence))
    return {
        "ok": proc["returncode"] == 0 and bool(artifacts.get("ok")),
        "stage": "real_fixture_inference",
        "attempted": True,
        "engine": engine,
        "process": proc,
        "artifacts": artifacts,
    }


def _run_seed(cfg: dict) -> int:
    value = cfg_get(cfg, "run.seed", 42)
    return int(42 if value is None else value)


def _smoke_artifacts(engine: str, out_dir: Path, expected_residue_count: int) -> dict[str, Any]:
    from . import screen

    report: dict[str, Any] = {"engine": engine, "out_dir": str(out_dir), "expected_residue_count": expected_residue_count}
    try:
        if engine == "chai1":
            valid = screen._valid_chai_artifacts(out_dir, expected_residue_count)
            structure = out_dir / "pred.model_idx_0.cif"
            pae_path = out_dir / "scores_extra_idx_0.npz"
            if not pae_path.is_file():
                pae_path = out_dir / "scores_extra.npz"
            confidence_path = out_dir / "scores.model_idx_0.npz"
        else:
            valid = screen._valid_boltz_artifacts(out_dir)
            prediction_dir = screen.find_boltz_prediction_dir(out_dir) or out_dir
            structures = sorted(prediction_dir.rglob("*.cif")) + sorted(prediction_dir.rglob("*.pdb"))
            structure = next(iter(structures), None)
            if structure is None:
                return {**report, "ok": False, "blocker": "structure artifact not found"}
            pae_path = structure.parent / f"pae_{structure.stem}.npz"
            confidence_path = structure.parent / f"confidence_{structure.stem}.json"

        residue_count = sum(len(sequence) for sequence in screen._structure_chain_sequences(structure).values())
        pae_summary = _npz_summary(pae_path)
        confidence_summary = _confidence_summary(confidence_path)
        ok = (
            bool(valid)
            and residue_count == expected_residue_count
            and pae_summary.get("finite", False)
            and tuple(pae_summary.get("shape", ())) == (expected_residue_count, expected_residue_count)
            and confidence_summary.get("exists", False)
        )
        return {
            **report,
            "ok": ok,
            "validator_ok": bool(valid),
            "residue_count": residue_count,
            "structure": _file_manifest(structure),
            "pae": pae_summary,
            "confidence": confidence_summary,
        }
    except Exception as exc:  # noqa: BLE001
        return {**report, "ok": False, "blocker": f"{exc.__class__.__name__}: {exc}"}


def _npz_summary(path: Path) -> dict[str, Any]:
    import numpy as np

    if not path.exists():
        return {"exists": False, "path": str(path)}
    with np.load(path) as data:
        key = "pae" if "pae" in data else "PAE" if "PAE" in data else next(iter(data.files), None)
        if key is None:
            return {"exists": True, "path": str(path), "keys": [], **_file_manifest(path), "finite": False}
        array = np.asarray(data[key])
        return {
            **_file_manifest(path),
            "exists": True,
            "keys": list(data.files),
            "selected_key": key,
            "shape": list(array.shape),
            "finite": bool(np.isfinite(array).all()),
            "min": float(np.nanmin(array)) if array.size else None,
            "max": float(np.nanmax(array)) if array.size else None,
        }


def _confidence_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}
    info = _file_manifest(path)
    if path.suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            info.update({"exists": True, "type": "json", "keys": sorted(payload)[:20], "is_dict": isinstance(payload, dict)})
            return info
        except Exception as exc:  # noqa: BLE001
            info.update({"exists": True, "type": "json", "parse_error": f"{exc.__class__.__name__}: {exc}"})
            return info
    npz = _npz_summary(path)
    npz["type"] = "npz"
    return npz


def _first_fasta_sequence(path: Path) -> str:
    chunks: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(">"):
            if chunks:
                break
            continue
        chunks.append(line.strip())
    return "".join(chunks)


def _skipped_smoke() -> dict[str, Any]:
    return {"ok": None, "stage": "not_requested", "attempted": False}


def _env_python(env: Path | None) -> Path | None:
    if env in (None, Path("")):
        return Path(sys.executable)
    for name in ("bin/python", "bin/python3", "Scripts/python.exe"):
        candidate = env / name
        if candidate.exists():
            return candidate
    return None


def _env_binary(env: Path | None, name: str) -> Path | None:
    if env:
        candidate = env / "bin" / name
        if candidate.exists():
            return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def _env_process_env(env: Path | None, extra_env: dict[str, str] | None = None) -> dict[str, str]:
    child_env = dict(os.environ)
    python = _env_python(env)
    if python is not None:
        child_env["PATH"] = f"{python.parent}{os.pathsep}{child_env.get('PATH', '')}"
        child_env.setdefault("PYTHONNOUSERSITE", "1")
    child_env.update(extra_env or {})
    return child_env


def _path_check(path: Path | None) -> dict[str, Any]:
    exists = bool(path and path.exists() and path.is_file() and path.stat().st_size > 0)
    return {"ok": exists, "stage": "file", "path": str(path) if path else None}


def _run(argv: list[str], timeout: float, env: dict[str, str] | None = None) -> dict[str, Any]:
    from .utils import shell

    start = time.time()
    try:
        proc = shell._budgeted_process(argv, cwd=None, env=env, timeout=shell.remaining_budget(timeout))
        return {
            "argv": argv,
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "")[-4000:],
            "seconds": round(time.time() - start, 3),
        }
    except subprocess.TimeoutExpired as exc:
        # A local probe limit is a structured failure. An exhausted execution
        # budget is a control signal and must reach the owner of the run.
        shell.remaining_budget()
        def tail(value):
            return (value.decode(errors="replace") if isinstance(value, bytes) else value or "")[-4000:]
        return {
            "argv": argv,
            "returncode": 124,
            "status": "timeout",
            "timeout_seconds": exc.timeout,
            "stdout": tail(exc.stdout) or str(exc),
            "stderr": tail(exc.stderr),
            "error": str(exc),
            "seconds": round(time.time() - start, 3),
        }
    except (shell.ExecutionCancelled, TimeoutError):
        raise
    except Exception as exc:  # noqa: BLE001
        return {
            "argv": argv,
            "returncode": 1,
            "stdout": str(exc),
            "seconds": round(time.time() - start, 3),
        }


def _check(name: str, ok: bool, required: bool, stage: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "required": bool(required), "stage": stage, "evidence": evidence}


def _loads(text: str) -> dict[str, Any]:
    """프로브 출력에서 마지막 JSON 객체를 읽는다.

    stderr 가 stdout 에 합쳐지므로 종료 시점 경고(예: torch/CUDA shutdown 메시지)가
    JSON 뒤에 붙을 수 있다. 마지막 줄만 보면 정상 환경도 "가중치/임포트 없음"으로
    잘못 판정된다(R9-6). 끝에서부터 파싱 가능한 줄을 찾는다.
    """
    lines = [line.strip() for line in str(text).strip().splitlines()]
    for line in reversed(lines):
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {"parse_error": "프로브 출력에서 JSON 객체를 찾지 못했습니다.", "raw": str(text)[:1000]}


def _cap_major(value: str) -> int:
    try:
        return int(str(value).split(".", 1)[0])
    except (TypeError, ValueError):
        return 0


def _cuda_at_least(version: str, major: int, minor: int) -> bool:
    try:
        left, right = str(version).split(".", 1)
        return (int(left), int(right.split(".", 1)[0])) >= (major, minor)
    except (TypeError, ValueError):
        return False


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))
