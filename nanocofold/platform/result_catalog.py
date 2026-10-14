"""Indexed completed-result metadata and conservative, content-based reuse.

Listing reads SQLite only. Registration does the per-run materialization once;
reuse verifies a recorded, bounded set of preserved scientific artifacts. This
catalog does not run models, contact an MSA service, or prune any source files.
"""
from __future__ import annotations

import base64
import contextlib
import copy
import fcntl
import hashlib
import importlib.metadata
import json
import math
import os
import re
import sqlite3
import stat
import time
from pathlib import Path

from .. import diagnostics, provenance
from ..config import cfg_get, resolve_path
from .inputs import INPUT_PATHS
from .paths import contained
from .results import ResultReader, TABLES

SCHEMA_VERSION = 1
RETENTIONS = {"full", "minimal"}
MAX_PAGE_SIZE = 100
SORT_FIELDS = {"created", "updated", "name", "mode", "retention", "status", "candidate_count", "model_count",
               "evaluated_model_count", "eligible_model_count", "elapsed_seconds", "storage_bytes"}
_NONNULL_SORT_FIELDS = {"created", "updated", "name", "mode", "retention", "status"}
# No blanket removal of unknown settings: adding an algorithmic option must
# change the identity even when an older catalog does not understand its name.
_DISPLAY_FIELDS = ("run.name", "run.outdir", "target.name", "input_review.target_name")
_CONTENT_FIELDS = INPUT_PATHS + (
    "design.framework", "design.boltzgen.scaffold", "score.ipsae.official_script",
    "mature.antifold.model_path", "specificity.cleaved_pdb",
    "input_review.residue_mapping_path", "input_review.mapping_path",
)
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_TARGET_ARTIFACTS = ("target_raw.pdb", "target_raw.cif", "target_prepared.pdb", "target_qc.json",
                     "hotspot_table.csv", "hotspot_sets.csv", "target_sequence.fasta", "target_msa.a3m")


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _field(config, dotted):
    section, key = dotted.split(".", 1)
    node = config.get(section)
    return node, key if isinstance(node, dict) else None


def _path(workspace, value):
    path = Path(value).expanduser()
    return (path if path.is_absolute() else workspace / path).resolve()


def _package_identity():
    root = Path(__file__).resolve().parents[1]
    files = {str(path.relative_to(root)): provenance.file_digest(path)
             for path in sorted(root.rglob("*"))
             if path.is_file() and path.suffix in {".py", ".json", ".yaml", ".sha256"}
             and "__pycache__" not in path.parts and "assets" not in path.parts}
    try:
        version = importlib.metadata.version("nanocofold")
    except importlib.metadata.PackageNotFoundError:
        version = "source"
    return {"version": version, "source_sha256": _digest(files), "source_files": len(files)}


def _runtime_identity(workspace, cfg, engine, reasons):
    # resolve_path in existing adapters uses their project root. Pass the
    # selected environment explicitly so this API also works in another cwd.
    spec = diagnostics._engine_spec(cfg, engine)
    env_value = spec.get("env")
    env = _path(workspace, env_value) if env_value else None
    runtime = provenance.runtime_identity(cfg, engine, env=env)
    python = runtime.get("python", {})
    if not isinstance(python, dict) or not _HEX.fullmatch(str(python.get("sha256", ""))):
        reasons.append(f"{engine}: runtime Python is not content-pinned")
    sources = runtime.get("source", {})
    # NanoCoFold adapters alone do not identify the external engine. At least
    # one external package/source must be discovered in the runtime identity.
    own = Path(__file__).resolve().parents[1]
    external = {path: digest for path, digest in sources.items()
                if not Path(path).resolve().is_relative_to(own)} if isinstance(sources, dict) else {}
    package_names = {"chai1": ("chai_lab",), "boltz2": ("boltz",), "colabfold": ("colabfold", "alphafold"),
                     "esm": ("esm",), "rfantibody": ("rfantibody",), "boltzgen": ("boltzgen",),
                     "antifold": ("antifold",), "anarci": ("anarci",)}.get(engine, (engine,))
    engine_sources = [path for path in external if any(
        name in Path(path).parts or any(part.lower().startswith(name + "-") and part.endswith(".dist-info")
                                      for part in Path(path).parts)
        or (engine == "rfantibody" and "rfantibody" in path.lower()) for name in package_names)]
    if not engine_sources or any(not _HEX.fullmatch(str(value)) for value in external.values()):
        reasons.append(f"{engine}: external engine source is not content-pinned")
    weights = runtime.get("weights", [])
    if spec.get("requires_weights"):
        if (not isinstance(weights, list) or not weights
                or any(not isinstance(row, dict) or not _HEX.fullmatch(str(row.get("sha256", ""))) for row in weights)):
            reasons.append(f"{engine}: required model/DB weights are not content-pinned")
        # A single discovered checkpoint is not sufficient for a multi-module
        # engine. Check the existing required-asset contract without inference.
        ready = diagnostics._engine_weights(cfg, engine, env)
        if ready.get("ok") is not True:
            reasons.append(f"{engine}: required local model/DB assets are incomplete")
    if engine == "boltz2" and runtime.get("support", {}).get("molecules", {}).get("file_count", 0) < 1:
        reasons.append("boltz2: molecule support database is not pinned")
    # Preserve compact digests, not thousands of source paths per result row.
    return {"sha256": _digest(runtime), "source_files": len(sources),
            "weights": [{"name": Path(row.get("path", "")).name, "sha256": row.get("sha256"),
                         "bytes": row.get("bytes", row.get("size"))}
                        for row in weights if isinstance(row, dict)],
            "python_sha256": python.get("sha256")}


def _target_msa_identity(cfg, reasons):
    """The target preparer consumes a separate cache, even with supplied PDBs."""
    from ..config import resolve_path
    from ..target_prep import resolve_target_sequence
    from ..utils.msa import sequence_hash, validate_target_a3m
    backend = str(cfg_get(cfg, "structure.msa.backend", "empty"))
    if backend == "empty":
        return {"mode": "empty"}
    sequence = resolve_target_sequence(cfg)
    # Follow the actual adapter's PROJECT_ROOT-relative cache resolution.
    cache = resolve_path(cfg, cfg_get(cfg, "structure.msa.cache_dir") or "data/msa_cache")
    alignment = cache / (sequence_hash(sequence) + ".a3m")
    if alignment.is_file():
        validate_target_a3m(alignment, sequence)
        return {"mode": "frozen_cache", "a3m_sha256": provenance.file_digest(alignment)}
    if backend == "cache":
        return {"mode": "cache_empty", "sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest()}
    reasons.append("Target preparation MSA search is not frozen; an exact cached target A3M is required")
    return {"mode": backend, "alignments_pinned": False}


def _verified_monomer_cache_identity(cache, reasons, required_sequences=None):
    """Hash the bounded cache the adapters consume, with receipt validation."""
    from ..colabfold_msa import cached_monomer, read_monomer
    cache=Path(cache)
    receipts=sorted(cache.glob('*/receipt.json'))
    if not 1 <= len(receipts) <= 10000:
        reasons.append("Verified monomer MSA cache is missing or exceeds the bounded identity limit")
        return {"mode":"cache","alignments_pinned":False}
    files={}
    sequences=set()
    for receipt in receipts:
        alignment=read_monomer(receipt.parent/'query.a3m',max_sequences=10000)
        entry=cached_monomer(cache,alignment.sequence)
        if not entry:
            raise ValueError("Monomer cache has no verified alignment")
        files[receipt.parent.name]={"a3m_sha256":entry['sha256'],"receipt_sha256":provenance.file_digest(receipt)}
        sequences.add(alignment.sequence)
    if required_sequences is not None:
        for sequence in required_sequences:
            cached_monomer(cache,sequence)
        if not set(required_sequences) <= sequences:
            raise ValueError("The consumed monomer cache lacks an exact input-chain alignment")
    return {"mode":"frozen_monomer_cache","alignments":files}


def _complex_msa_identity(cfg, reasons):
    from ..complex import proteins, validate_settings
    settings=validate_settings(cfg_get(cfg,'complex',{}))
    if settings['msa']=='empty':
        return {'mode':'empty'}
    requested=settings.get('msa_source','cache')
    if requested!='cache':
        reasons.append("Complex comparison MSA acquisition is mutable/unpinned; exact verified cached monomers are required for reuse")
        return {'mode':requested,'complex_comparison_acquisition':True,'alignments_pinned':False}
    entries=proteins(cfg_get(cfg,'complex.proteins'))
    # complex_comparison.configure overwrites the predictor MSA source with
    # these reviewed settings and prepare() consumes this PROJECT_ROOT cache.
    cache=resolve_path(cfg,cfg_get(cfg,'screen.colabfold.msa_cache_dir','msa/monomer_cache'))
    result=_verified_monomer_cache_identity(cache,reasons,{p['sequence'] for p in entries})
    result['complex_comparison_acquisition']=True
    return result


def _msa_identity(workspace, cfg, engines, reasons):
    """Identify immutable MSAs/DBs locally; never turn a URL into a DB version."""
    if cfg_get(cfg, "run.mock", False):
        return {"mode": "mock"}
    if cfg_get(cfg, "run.mode") == "complex":
        return _complex_msa_identity(cfg,reasons)
    target = _target_msa_identity(cfg, reasons)
    if (not cfg_get(cfg, "target.pdb") and cfg_get(cfg, "structure.provider") == "chai1"
            and cfg_get(cfg, "structure.chai1.msa_mode", "single") == "msa"):
        reasons.append("Target Chai-1 public MSA database is mutable and not content-pinned")
    if ("chai1" in engines and not cfg_get(cfg, "screen.comparison.enabled", False)
            and cfg_get(cfg, "screen.msa_mode", "single") == "msa"):
        reasons.append("Screen Chai-1 public MSA database is mutable and not content-pinned")
    if cfg_get(cfg, "screen.binder_msa", False) and cfg_get(cfg, "structure.msa.backend", "empty") != "empty":
        # Candidate-dependent searches use this separate target/binder cache,
        # not ColabFold's frozen multimer collection. Do not certify them by a
        # predictor collection that the legacy binder search does not consume.
        reasons.append("Candidate-dependent binder MSA cache/search is not pinned by the predictor collection")
    cf = cfg_get(cfg, "screen.colabfold", {})
    requested = cf.get("msa_source", "cache")
    comparison = cfg_get(cfg, "screen.comparison.enabled", False) is True
    if comparison and requested != "cache":
        # engine_comparison.prepare_msa acquires online monomers and rebuilds
        # its own collection. It does not consume the caller's msa_dir, even
        # when that directory contains a perfectly valid frozen collection.
        reason = ("Comparison public MSA service/database is mutable; supplied msa_dir is not the consumed source"
                  if requested == "public_api" else
                  "Comparison online MSA acquisition is not frozen; supplied msa_dir is not the consumed source")
        reasons.append(reason)
        return {"mode": requested, "comparison_acquisition": True,
                "alignments_pinned": False, "target": target}
    uses_cf = "colabfold" in engines
    uses_msa = (uses_cf or cfg_get(cfg, "screen.msa_mode", "single") != "single"
                or cfg_get(cfg, "screen.binder_msa", False)
                or (not cfg_get(cfg, "target.pdb") and cfg_get(cfg, "structure.msa.backend", "empty") != "empty"))
    if not uses_msa and not comparison:
        return {"mode": "single_sequence", "target": target}
    directory = None if comparison else cf.get("msa_dir")
    if (not comparison and not directory and not uses_cf and requested == "cache"
            and cfg_get(cfg, "screen.msa_mode", "single") in {"single", "local"}
            and not cfg_get(cfg, "screen.binder_msa", False)):
        # This path consumes only the independently verified target cache.
        # A missing cache with backend=cache is an explicit empty-MSA choice.
        return {"mode": "target_cache" if target["mode"] == "frozen_cache" else "single_sequence", "target": target}
    if directory:
        from ..colabfold_msa import Collection, read_multimer
        collection = Collection(_path(workspace, directory), max_sequences=cf.get("msa_max_sequences", 2048))
        files = {}
        for group in ("static_chains", "parents", "queries"):
            for key, entry in collection.manifest.get(group, {}).items():
                path = collection._path(entry)
                digest = provenance.file_digest(path)
                if digest != entry.get("sha256"):
                    raise ValueError("Frozen MSA content differs from the recorded hash")
                if group == "queries":
                    read_multimer(path, entry.get("chains"), expected_sha256=digest,
                                  max_sequences=cf.get("msa_max_sequences", 2048))
                files[group + ":" + str(key)] = digest
        if not files:
            reasons.append("MSA manifest contains no frozen alignments")
        result = {"mode": "precomputed", "manifest_sha256": collection.manifest_sha256,
                  "alignment_sha256": files, "pairing": collection.manifest.get("pairing"), "target": target}
        db_manifest = cfg_get(cfg, "structure.msa.database_manifest") or cf.get("database_manifest")
        if db_manifest:
            result["database_manifest_sha256"] = provenance.file_digest(_path(workspace, db_manifest))
        return result
    if (uses_cf or comparison) and requested == "cache" and (cf.get("msa_cache_dir") or comparison):
        cache_value = cf.get("msa_cache_dir") or "msa/monomer_cache"
        cache = resolve_path(cfg, cache_value) if comparison else _path(workspace, cache_value)
        result=_verified_monomer_cache_identity(cache,reasons)
        if result['mode']=='frozen_monomer_cache':result['target']=target
        return result
    if requested in {"public_api", "public_mmseqs2"} or cfg_get(cfg, "structure.allow_public_msa_server", False):
        reasons.append("Public MSA service/database is mutable; frozen local A3Ms are required for exact reuse")
        return {"mode": requested, "exact_database_identity": False}
    # Local search still needs the exact DB ledger. Remote readiness is not
    # probed here: a network read must not submit work or silently relax gates.
    db = cfg_get(cfg, "structure.msa.db")
    manifest = cfg_get(cfg, "structure.msa.database_manifest") or cfg_get(cfg, "screen.colabfold.database_manifest")
    if manifest or db:
        path = _path(workspace, manifest) if manifest else _path(workspace, db).parent / "nanocofold_db_manifest.json"
        if not path.is_file():
            reasons.append("Local MSA database manifest is missing")
            return {"mode": "local", "database_pinned": False}
        data = json.loads(path.read_text(encoding="utf-8"))
        files = data.get("files")
        if (not isinstance(files, dict) or not files or data.get("status") != "ready"
                or any(not isinstance(row, dict) or not _HEX.fullmatch(str(row.get("sha256", ""))) for row in files.values())):
            reasons.append("Local MSA DB manifest lacks complete content hashes")
        # Hashing a sealed manifest identifies the DB, but not an unfrozen
        # search's actual alignments. No reuse until those outputs are frozen.
        reasons.append("Local MSA alignments are not frozen; preserve a verified precomputed MSA collection first")
        return {"mode": "local", "database_manifest_sha256": provenance.file_digest(path)}
    reasons.append("MSA runtime/database/alignments are not pinned")
    return {"mode": requested, "exact_database_identity": False}


def build_identity(workspace, cfg, gpu):
    """Return a provenance key and whether it is sufficiently pinned for reuse.

    The key includes seed, all unknown configuration, input file bytes, package
    and adapter sources, effective runtime/weights and GPU selection. Returning
    a digest is not a claim of deterministic scientific reproducibility.
    """
    workspace = Path(workspace).resolve()
    if not isinstance(cfg, dict):
        raise ValueError("설정은 JSON 객체여야 합니다.")
    canonical = copy.deepcopy(cfg)
    reasons, inputs = [], {}
    for dotted in _DISPLAY_FIELDS:
        node, key = _field(canonical, dotted)
        if key:
            node.pop(key, None)
    mode = str(cfg_get(cfg, "run.mode", "design"))
    for dotted in _CONTENT_FIELDS:
        section, key = dotted.split(".", 1)
        node = canonical.get(section, {})
        # Some nested settings (BoltzGen scaffold, AntiFold path) use a third
        # component. Keep the original shape and replace only the file value.
        keys = key.split(".")
        for part in keys[:-1]:
            node = node.get(part, {}) if isinstance(node, dict) else {}
        key = keys[-1]
        value = node.get(key) if isinstance(node, dict) else None
        if value:
            try:
                path = _path(workspace, value)
                info = {"sha256": provenance.file_digest(path), "bytes": path.stat().st_size}
                node[key] = {"content": info}
                inputs[dotted] = info
                if dotted in {"target.pdb", "input_review.reference_path"}:
                    for name in ("residue_mapping.csv", "input_review.json"):
                        receipt = path.parent / name
                        if receipt.is_file():
                            inputs[dotted + ":" + name] = {"sha256": provenance.file_digest(receipt),
                                                         "bytes": receipt.stat().st_size}
            except (ValueError, TypeError, OSError) as exc:
                # A default design scaffold is irrelevant to a screen-only
                # request, but remains in the config key. Fail closed only
                # when the unavailable file can be consumed by this mode.
                mock_unused = cfg_get(cfg, "run.mock", False) is True and dotted not in INPUT_PATHS and dotted not in {
                    "input_review.residue_mapping_path", "input_review.mapping_path"}
                active = not (mock_unused or dotted.startswith("design.") and mode != "design"
                              or dotted.startswith("mature.") and mode != "mature"
                              or dotted.startswith("specificity.") and not cfg_get(cfg, "specificity.enabled", False))
                if active:
                    reasons.append(f"{dotted}: input file identity unavailable ({type(exc).__name__})")
                node[key] = {"unavailable": True, "configured": str(value)}
    # Cfg already contains its effective defaults. Never merge different
    # defaults here or mutate the pinned configuration used by the worker.
    engines = sorted(set(diagnostics._mode_engines(cfg, mode) + diagnostics._followup_engines(cfg, mode)))
    runtimes = {}
    if cfg_get(cfg, "run.mock", False) is not True:
        for engine in engines:
            try:
                runtimes[engine] = _runtime_identity(workspace, cfg, engine, reasons)
            except (ValueError, TypeError, OSError, KeyError) as exc:
                reasons.append(f"{engine}: runtime identity unavailable ({type(exc).__name__})")
                runtimes[engine] = {"unavailable": True}
        provider = str(cfg_get(cfg, "structure.provider", "none"))
        if mode != "complex" and not cfg_get(cfg, "target.pdb") and provider in {"boltz2", "chai1"}:
            # Target preparation can use a different env than screening. The
            # same engine name does not make those installations identical.
            target_cfg = copy.deepcopy(cfg)
            target_cfg.setdefault("screen", {})["boltz2_env" if provider == "boltz2" else "env"] = cfg_get(
                cfg, f"structure.{provider}.env")
            try:
                runtimes["target:" + provider] = _runtime_identity(workspace, target_cfg, provider, reasons)
            except (ValueError, TypeError, OSError, KeyError) as exc:
                reasons.append(f"target:{provider}: runtime identity unavailable ({type(exc).__name__})")
                runtimes["target:" + provider] = {"unavailable": True}
    if cfg_get(cfg, "run.dry_run", False):
        reasons.append("Dry-run output cannot satisfy completed computation reuse")
    gpu_value = str(gpu)
    if cfg_get(cfg, "run.mock", False) is not True and not re.fullmatch(
            r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", gpu_value):
        reasons.append("GPU selection is not an immutable UUID")
    try:
        msa = _msa_identity(workspace, cfg, engines, reasons)
    except (ValueError, TypeError, OSError, KeyError, ImportError) as exc:
        reasons.append(f"MSA identity unavailable ({type(exc).__name__})")
        msa = {"unavailable": True}
    canonical_cf = canonical.get("screen", {}).get("colabfold", {})
    if isinstance(canonical_cf, dict):
        for name in ("msa_dir", "msa_cache_dir"):
            if canonical_cf.get(name) and msa.get("mode") in {"precomputed", "frozen_monomer_cache"}:
                canonical_cf[name] = {"frozen_content_sha256": _digest(msa)}
    document = {"schema_version": SCHEMA_VERSION, "config": canonical, "inputs": inputs,
                "package": _package_identity(), "engines": engines, "runtime": runtimes,
                "platform_python": {"version": os.sys.version, "sha256": provenance.file_digest(os.sys.executable)},
                "gpu": gpu_value, "msa": msa}
    return {"digest": _digest(document), "document": document, "reusable": not reasons,
            "reasons": list(dict.fromkeys(reasons))}


def _valid_identity(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or not isinstance(value.get("document"), dict)
            or not _HEX.fullmatch(str(value.get("digest", "")))
            or value["digest"] != _digest(value["document"])
            or type(value.get("reusable")) is not bool
            or not isinstance(value.get("reasons", []), list)):
        raise ValueError("결과 재사용 identity가 없거나 손상되었습니다.")
    return value


def _storage(root):
    total = 0
    for folder, directories, files in os.walk(root, followlinks=False):
        directories[:] = [name for name in directories if not (Path(folder) / name).is_symlink()]
        for name in files:
            path = Path(folder) / name
            if not path.is_symlink():
                total += path.stat().st_size
    return total


def _elapsed(reader):
    for record in (reader.platform_status, reader.state.get("execution", {})):
        value = record.get("elapsed_seconds")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
            return value
        started, finished = record.get("started"), record.get("finished")
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (started, finished)):
            return max(0, finished - started)
    return None


def _input_files_intact(reader, identity):
    """Verify source snapshots/review receipts without rehashing engine assets."""
    for dotted, expected in identity["document"].get("inputs", {}).items():
        field, separator, auxiliary = dotted.partition(":")
        value = cfg_get(reader.config, field)
        if not value:
            return False
        # CLI adapters resolve relative scientific inputs against PROJECT_ROOT.
        # The catalog may live in a separate --reuse-workspace; a same-named
        # file there is never a substitute for the file consumed by the run.
        # Platform inputs are already saved as absolute immutable snapshots.
        path = resolve_path(reader.config, value).resolve()
        if separator:
            path = path.parent / auxiliary
        if (not path.is_file() or path.stat().st_size != expected["bytes"]
                or provenance.file_digest(path) != expected["sha256"]):
            return False
    return True


def _scientific_config_matches(cfg, identity):
    """Compare saved inputs/settings without rebuilding historical runtimes."""
    inputs = identity["document"].get("inputs")
    if not isinstance(inputs, dict):
        return False
    canonical = copy.deepcopy(cfg)
    for dotted in _DISPLAY_FIELDS:
        node, key = _field(canonical, dotted)
        if key:
            node.pop(key, None)
    for dotted in _CONTENT_FIELDS:
        keys = dotted.split(".")
        node = canonical
        for key in keys[:-1]:
            node = node.get(key, {}) if isinstance(node, dict) else {}
        value = node.get(keys[-1]) if isinstance(node, dict) else None
        if value:
            node[keys[-1]] = ({"content": inputs[dotted]} if dotted in inputs
                             else {"unavailable": True, "configured": str(value)})
    msa = identity["document"].get("msa", {})
    cf = canonical.get("screen", {}).get("colabfold", {})
    if isinstance(cf, dict) and isinstance(msa, dict) and msa.get("mode") in {"precomputed", "frozen_monomer_cache"}:
        for name in ("msa_dir", "msa_cache_dir"):
            if cf.get(name):
                cf[name] = {"frozen_content_sha256": _digest(msa)}
    return canonical == identity["document"].get("config")


def _retained_input_issues(root, record, identity):
    """Bind the archived input bytes to the original execution identity.

    An archive manifest proves that its files are intact, not that those files
    were the inputs used by an earlier execution. Never infer that linkage
    from the current external paths or retroactively rebuild runtime identity.
    """
    inputs = identity["document"].get("inputs")
    if not isinstance(inputs, dict) or any(not isinstance(field, str) for field in inputs):
        return ["Retained execution input identity is unavailable"]
    configured = {field for field in INPUT_PATHS if cfg_get(record["config"], field)}
    artifacts = record["provenance"].get("input_artifacts", [])
    if not isinstance(artifacts, list):
        return ["Retained input artifact receipts are invalid"]
    manifest = json.loads(contained(root, "retained/manifest.json", exists=True).read_text())
    files = {entry["path"]: entry for entry in manifest["files"]}
    receipts, issues = {}, []
    proof = record["provenance"].get("execution_inputs", {})
    proof_valid = (isinstance(proof, dict) and proof.get("original_input_verified") is True
                   and proof.get("status") == "matches_recorded_execution_identity"
                   and isinstance(proof.get("identity_digests"), list)
                   and identity["digest"] in proof["identity_digests"]
                   and isinstance(proof.get("checked_fields"), list)
                   and proof.get("missing_fields") == [])
    if not proof_valid:
        issues.append("Original execution identity was not verified when the retained archive was created")
    if not _scientific_config_matches(record["config"], identity):
        issues.append("Retained scientific configuration differs from the original execution identity")
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("field"), str):
            issues.append("Retained input artifact receipt is invalid")
            continue
        field = artifact["field"]
        stored = files.get(artifact.get("path")) if isinstance(artifact.get("path"), str) else None
        if (field in receipts or artifact.get("status") != "retained" or stored is None
                or artifact.get("sha256") != stored["sha256"]
                or artifact.get("size") != stored["size"]):
            issues.append(f"{field}: retained input content receipt is invalid")
        receipts[field] = artifact
    # Small immutable receipts can preserve proof for heavy auxiliary/model
    # inputs without copying their bytes. Primary scientific input files must
    # always use the real archive payload above, never a hash-only substitute.
    auxiliary = record["provenance"].get("input_identity_receipts", [])
    if not isinstance(auxiliary, list):
        issues.append("Retained auxiliary input receipts are invalid")
    elif proof_valid:
        for receipt in auxiliary:
            if not isinstance(receipt, dict) or not isinstance(receipt.get("field"), str):
                issues.append("Retained auxiliary input receipt is invalid")
                continue
            field = receipt["field"]
            if field in INPUT_PATHS:
                continue
            if (field in receipts or receipt.get("status") != "hash_verified_at_retention"
                    or not isinstance(receipt.get("identity_digests"), list)
                    or identity["digest"] not in receipt["identity_digests"]
                    or field not in proof["checked_fields"]):
                issues.append(f"{field}: retained auxiliary input proof is invalid")
            else:
                receipts[field] = receipt
    for field in sorted(configured | set(receipts) | set(inputs)):
        expected, archived = inputs.get(field), receipts.get(field)
        if field in INPUT_PATHS and field not in configured:
            issues.append(f"{field}: retained input is not configured for the recorded execution")
        if (not isinstance(expected, dict) or not _HEX.fullmatch(str(expected.get("sha256", "")))
                or type(expected.get("bytes")) is not int or expected["bytes"] < 0):
            issues.append(f"{field}: original execution input identity is unavailable")
        elif archived is None:
            # Heavy auxiliary/model inputs are not copied by the minimal
            # policy. Missing original-input evidence disables automatic
            # linking without affecting the browsable retained result.
            issues.append(f"{field}: retained input evidence is unavailable")
        elif expected["sha256"] != archived.get("sha256") or expected["bytes"] != archived.get("size"):
            issues.append(f"{field}: retained input differs from the original execution identity")
    return list(dict.fromkeys(issues))


class ResultCatalog:
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.root = self.workspace / ".nanocofold"
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "result_catalog.sqlite3"
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS identities (
                    digest TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS results (
                    id TEXT PRIMARY KEY, created REAL NOT NULL, updated REAL NOT NULL,
                    mode TEXT NOT NULL, retention TEXT NOT NULL, status TEXT NOT NULL,
                    identity_digest TEXT, can_reuse INTEGER NOT NULL, name TEXT NOT NULL,
                    candidate_count INTEGER, model_count INTEGER, evaluated_model_count INTEGER,
                    eligible_model_count INTEGER, elapsed_seconds REAL, storage_bytes INTEGER,
                    payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS artifacts (
                    result_id TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
                    bytes INTEGER NOT NULL, PRIMARY KEY(result_id,path),
                    FOREIGN KEY(result_id) REFERENCES results(id) ON DELETE CASCADE);
                CREATE INDEX IF NOT EXISTS results_created ON results(created DESC,id DESC);
                CREATE INDEX IF NOT EXISTS results_mode_created ON results(mode,created DESC,id DESC);
                CREATE INDEX IF NOT EXISTS results_retention_created ON results(retention,created DESC,id DESC);
                CREATE INDEX IF NOT EXISTS results_mode_retention_created ON results(mode,retention,created DESC,id DESC);
                CREATE INDEX IF NOT EXISTS results_reuse ON results(identity_digest,can_reuse,status,updated DESC,id DESC);
            """)
            # This also handles catalogs created during an older installation;
            # adding materialized fields never rereads the source directories.
            columns = {row[1] for row in connection.execute("PRAGMA table_info(results)")}
            for name in SORT_FIELDS - {"created", "updated", "name", "mode", "retention", "status"}:
                if name not in columns:
                    connection.execute(f"ALTER TABLE results ADD COLUMN {name} " + ("REAL" if name == "elapsed_seconds" else "INTEGER"))
            for name in SORT_FIELDS:
                if name in _NONNULL_SORT_FIELDS:
                    connection.execute(f"CREATE INDEX IF NOT EXISTS results_sort_{name} ON results({name},id)")
                    continue
                for direction in ("ASC", "DESC"):
                    connection.execute(f"CREATE INDEX IF NOT EXISTS results_sort_{name}_{direction.lower()} "
                                       f"ON results(({name} IS NULL) ASC,{name} {direction},id {direction})")

    @contextlib.contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def register(self, identifier, run_root, identity=None, state="complete", retention="full"):
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", identifier):
            raise ValueError("잘못된 결과 ID입니다.")
        if retention not in RETENTIONS:
            raise ValueError("결과 보존 방식은 full/minimal 중 하나여야 합니다.")
        identity = _valid_identity(identity)
        root = contained(self.workspace, run_root, exists=True)
        reader = ResultReader(root)
        record = None
        if reader.retained:
            # Discovering an archive after catalog recovery must not relabel
            # the verified retained view as a full native execution. The
            # reader has already verified the immutable archive manifest.
            retention = "minimal"
        summary = reader.summary()
        rows = reader.all_rows()
        observations = reader.observations().get("rows", [])
        paths = {relative for relative in ("run_config.yaml", "run_state.json", "platform_status.json",
                 "complex_results.json", "models.csv", "summary.json", "result.json", "input_review.json",
                 "residue_mapping.csv", "02_comparison/comparison_status.json", "03_score/cascade_summary.json",
                 "05_final/selected_candidates.csv", "05_final/selected_candidates.fasta") + TABLES
                 if (root / relative).is_file()}
        essential_complete = bool(rows) and all((root / relative).is_file() for relative in ("run_config.yaml", "run_state.json"))
        if retention == "full":
            paths.update("01_target/" + name for name in _TARGET_ARTIFACTS if (root / "01_target" / name).is_file())
            target_phase = reader.state.get("phases", {}).get("target", {})
            for name, expected in target_phase.get("artifact_hashes", {}).items():
                try:
                    path = contained(root, Path("01_target") / name, exists=True)
                    # Existing target stage receipts use a 16-character
                    # SHA256 prefix; this catalog always stores the full hash.
                    expected = str(expected)
                    if (not re.fullmatch(r"(?:[0-9a-f]{16}|[0-9a-f]{64})", expected)
                            or not provenance.file_digest(path).startswith(expected)):
                        essential_complete = False
                    paths.add(str(path.relative_to(root)))
                except (ValueError, TypeError, OSError):
                    essential_complete = False
        for row in rows + observations:
            for key in ("cif_path", "colabfold_cif_path", "structure_file"):
                if row.get(key):
                    try:
                        path = reader.artifact_path(row[key])
                        if not path.is_file():
                            essential_complete = False
                            continue
                        paths.add(str(path.relative_to(root)))
                        if key != "structure_file":
                            prediction = reader._prediction_for_path(path)
                            for associated in (prediction.confidence_json, prediction.scores_npz, prediction.pae_npz):
                                if associated and associated.is_file():
                                    paths.add(str(associated.relative_to(root)))
                    except (ValueError, OSError):
                        essential_complete = False
            if row.get("source_table") and (root / row["source_table"]).is_file():
                paths.add(str(contained(root, row["source_table"]).relative_to(root)))
            if row.get("evaluated") is True and row.get("structure_available") is not True:
                essential_complete = False
        if retention == "minimal":
            # Original native paths intentionally disappear after a flush.
            # Only a verified compact archive can satisfy a retained-view hit.
            from .retention import retained_record
            record = retained_record(root)
            if record is None:
                raise ValueError("검증된 최소 보존 결과가 없습니다.")
            compact = json.loads(contained(root, "retained/manifest.json", exists=True).read_text())
            paths = {entry["path"] for entry in compact["files"]} | {"retained/manifest.json"}
            paths.update(relative for relative in ("run_config.yaml", "run_state.json", "platform_status.json")
                         if (root / relative).is_file())
            essential_complete = bool(rows)
        manifest = []
        for relative in sorted(paths):
            path = contained(root, relative, exists=True)
            manifest.append((identifier, relative, provenance.file_digest(path), path.stat().st_size))
        if identity is None:
            # Metadata refresh must not erase a worker's pinned provenance.
            # Nor may it attach old provenance to files changed by a rerun.
            with self.connect() as connection:
                previous = connection.execute("SELECT payload,identity_digest FROM results WHERE id=?", (identifier,)).fetchone()
                if previous and previous["identity_digest"]:
                    old = json.loads(previous["payload"])
                    old_files = connection.execute("SELECT path,sha256,bytes FROM artifacts WHERE result_id=?", (identifier,)).fetchall()
                    fresh = {row[1]: (row[2], row[3]) for row in manifest}
                    intact = bool(old_files) and all(fresh.get(row["path"]) == (row["sha256"], row["bytes"]) for row in old_files)
                    if old.get("run_root") == str(root) and old.get("retention") == retention and intact:
                        saved = connection.execute("SELECT payload FROM identities WHERE digest=?", (previous["identity_digest"],)).fetchone()
                        identity = _valid_identity(json.loads(saved["payload"])) if saved else None
        identity_issues = []
        if identity:
            if retention == "minimal":
                identity_issues = _retained_input_issues(root, record, identity)
            elif not _scientific_config_matches(reader.config, identity):
                identity_issues = ["Source scientific configuration differs from the original execution identity"]
        status = str(state if state != "complete" else summary.get("status", "incomplete"))
        complete = state == "complete" and summary.get("complete") is True and not reader.issues
        if state == "complete" and not complete and status == "complete":
            status = "incomplete"
        created = reader.platform_status.get("created") or reader.state.get("execution", {}).get("started")
        if not isinstance(created, (int, float)) or isinstance(created, bool) or not math.isfinite(created):
            created = root.stat().st_mtime
        candidate_ids = {str(row.get("source_sequence_id") or row.get("candidate_id")) for row in rows}
        payload = {"id": identifier, "name": str(summary.get("name") or identifier), "run_root": str(root),
                   "mode": str(summary.get("mode") or "unknown"), "mock": cfg_get(reader.config, "run.mock", False) is True,
                   "status": status, "complete": complete, "retention": retention,
                   "full_execution_available": retention == "full", "candidate_count": len(candidate_ids),
                   "model_count": sum(row.get("structure_available") is True or bool(row.get("cif_path")) for row in observations),
                   "observation_count": len(observations), "evaluated_model_count": summary.get("scored_total", 0),
                   "eligible_model_count": summary.get("eligible_total"),
                   "failed_model_count": summary.get("failed_candidates", 0), "elapsed_seconds": _elapsed(reader),
                   "storage_bytes": _storage(root), "storage_measured_at": time.time(),
                   "identity_digest": identity["digest"] if identity else None,
                   "can_reuse": bool(complete and essential_complete and not identity_issues and identity and identity["reusable"]),
                   "reuse_kind": "view_only" if retention == "minimal" else "full",
                   "reuse_reasons": (identity.get("reasons", []) + identity_issues) if identity else ["Execution identity was not recorded"],
                   "issues": list(dict.fromkeys(reader.issues)), "created": float(created), "updated": time.time()}
        if not essential_complete:
            payload["reuse_reasons"] = payload["reuse_reasons"] + ["Required result artifacts are missing"]
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT created,updated FROM results WHERE id=?", (identifier,)).fetchone()
            if existing:
                payload["created"] = existing["created"]
                payload["updated"] = max(payload["updated"], existing["updated"] + .000001)
            if identity:
                connection.execute("INSERT OR IGNORE INTO identities(digest,payload) VALUES(?,?)", (identity["digest"], _json(identity)))
            connection.execute("""INSERT INTO results(id,created,updated,mode,retention,status,identity_digest,can_reuse,name,payload)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,mode=excluded.mode,
                retention=excluded.retention,status=excluded.status,identity_digest=excluded.identity_digest,
                can_reuse=excluded.can_reuse,name=excluded.name,payload=excluded.payload""",
                (identifier, payload["created"], payload["updated"], payload["mode"], retention, status,
                 payload["identity_digest"], int(payload["can_reuse"]), payload["name"], _json(payload)))
            connection.execute("UPDATE results SET candidate_count=?,model_count=?,evaluated_model_count=?,eligible_model_count=?,elapsed_seconds=?,storage_bytes=? WHERE id=?",
                (payload["candidate_count"], payload["model_count"], payload["evaluated_model_count"], payload["eligible_model_count"],
                 payload["elapsed_seconds"], payload["storage_bytes"], identifier))
            connection.execute("DELETE FROM artifacts WHERE result_id=?", (identifier,))
            connection.executemany("INSERT INTO artifacts(result_id,path,sha256,bytes) VALUES(?,?,?,?)", manifest)
        return payload

    def get(self, identifier):
        with self.connect() as connection:
            row = connection.execute("SELECT payload,identity_digest FROM results WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError("결과가 없습니다.")
            result = json.loads(row["payload"])
            identity = connection.execute("SELECT payload FROM identities WHERE digest=?", (row["identity_digest"],)).fetchone()
        result["identity"] = json.loads(identity["payload"]) if identity else None
        return result

    def store_identity(self, identity):
        identity = _valid_identity(identity)
        if identity is None:
            raise ValueError("실행 identity가 필요합니다.")
        with self.connect() as connection:
            connection.execute("INSERT OR IGNORE INTO identities(digest,payload) VALUES(?,?)", (identity["digest"], _json(identity)))
        return identity["digest"]

    def get_identity(self, digest):
        with self.connect() as connection:
            row = connection.execute("SELECT payload FROM identities WHERE digest=?", (digest,)).fetchone()
        if row is None:
            raise KeyError("실행 identity가 없습니다.")
        return _valid_identity(json.loads(row["payload"]))

    def list(self, limit=25, cursor=None, search="", mode="", retention="", sort="created", descending=True):
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError("페이지 크기는 1–100이어야 합니다.")
        if any(not isinstance(value, str) for value in (search, mode, retention)) or len(search) > 200:
            raise ValueError("결과 검색 조건 형식이 잘못되었습니다.")
        if mode and mode not in {"complex", "design", "screen", "mature", "unknown"}:
            raise ValueError("잘못된 분석 모드입니다.")
        if retention and retention not in RETENTIONS:
            raise ValueError("잘못된 결과 보존 방식입니다.")
        if not isinstance(sort, str) or sort not in SORT_FIELDS or type(descending) is not bool:
            raise ValueError("결과 정렬 조건 형식이 잘못되었습니다.")
        filters = {"search": search, "mode": mode, "retention": retention, "sort": sort, "descending": descending}
        clauses, parameters = [], []
        position = None
        for key, value in (("mode", mode), ("retention", retention)):
            if value:
                clauses.append(key + "=?")
                parameters.append(value)
        if search:
            # Bound output and avoid LIKE wildcard expansion from user input.
            # Arbitrary substring search may scan metadata; never source files.
            clauses.append("(instr(lower(name),lower(?))>0 OR instr(lower(id),lower(?))>0)")
            parameters.extend((search, search))
        if cursor:
            try:
                if not isinstance(cursor, str) or len(cursor) > 2048:
                    raise ValueError
                position = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
                if (not isinstance(position, dict) or position.get("filters") != filters
                        or not isinstance(position.get("id"), str) or "value" not in position
                        or isinstance(position["value"], (list, dict, bool))
                        or isinstance(position["value"], float) and not math.isfinite(position["value"])):
                    raise ValueError
            except (ValueError, UnicodeError, TypeError) as exc:
                raise ValueError("페이지 커서가 잘못되었거나 검색 조건이 변경되었습니다.") from exc
        direction = "DESC" if descending else "ASC"
        comparison = "<" if descending else ">"
        def query(connection, additions, values, count, null_order=False):
            predicates = clauses + additions
            where = " WHERE " + " AND ".join(predicates) if predicates else ""
            prefix = f"({sort} IS NULL) ASC," if null_order else ""
            return connection.execute(f"SELECT id,{sort} AS sort_value,payload FROM results" + where +
                                      f" ORDER BY {prefix}{sort} {direction},id {direction} LIMIT ?",
                                      (*parameters, *values, count)).fetchall()
        with self.connect() as connection:
            if sort in _NONNULL_SORT_FIELDS:
                if position and position["value"] is None:
                    raise ValueError("페이지 커서 정렬 값이 잘못되었습니다.")
                additions = [f"({sort},id){comparison}(?,?)"] if position else []
                values = [position["value"], position["id"]] if position else []
                rows = query(connection, additions, values, limit + 1)
            else:
                rows = []
                if not position or position["value"] is not None:
                    additions, values = [f"({sort} IS NULL)=0"], []
                    if position:
                        additions.append(f"({sort},id){comparison}(?,?)")
                        values.extend([position["value"], position["id"]])
                    rows = query(connection, additions, values, limit + 1)
                if len(rows) <= limit:
                    additions, values = [f"({sort} IS NULL)=1"], []
                    if position and position["value"] is None:
                        additions.append(f"id{comparison}?")
                        values.append(position["id"])
                    rows.extend(query(connection, additions, values, limit + 1 - len(rows)))
        next_cursor = None
        if len(rows) > limit:
            position = rows[limit - 1]
            next_cursor = base64.urlsafe_b64encode(_json({"id": position["id"], "value": position["sort_value"],
                                                        "filters": filters}).encode()).decode()
        return {"items": [json.loads(row["payload"]) for row in rows[:limit]], "next_cursor": next_cursor}

    def _source_active(self, root):
        """Bounded, read-only lookup; never initialize/import the job manager."""
        db = self.root / "jobs.sqlite3"
        if not db.exists():
            return False
        if db.is_symlink() or not db.is_file():
            return True
        connection = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            # Require the source-root index so older/malformed stores never
            # turn this lookup into an unbounded scan of historical aliases.
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='jobs_active_run_dir'").fetchone():
                return True
            return connection.execute("""SELECT 1 FROM jobs INDEXED BY jobs_active_run_dir
                WHERE json_extract(payload,'$.run_dir')=? AND state IN ('queued','running','cancelling') LIMIT 1""",
                (str(root),)).fetchone() is not None
        finally:
            connection.close()

    @contextlib.contextmanager
    def _source_lease(self, root):
        """Hold the writer's existing inode without changing its contents."""
        path = root / ".run.lock"
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise ValueError("Source lock owner/type is invalid")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            current = path.stat(follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError("Source lock inode changed")
            yield
        finally:
            # close also releases flock after an acquisition failure.
            os.close(descriptor)

    @contextlib.contextmanager
    def reuse_lease(self, identity_digest):
        """Yield a verified source while holding its writer lock through commit.

        The caller must commit the alias/link inside this context. Missing or
        active locks produce no hit; catalog registration never creates locks.
        """
        if not isinstance(identity_digest, str) or not _HEX.fullmatch(identity_digest):
            yield None
            return
        # At most 100 candidates per request; stale/corrupt entries are
        # invalidated instead of repeatedly returning a false cache hit.
        with self.connect() as connection:
            rows = connection.execute("""SELECT id,updated,payload FROM results WHERE identity_digest=?
                AND can_reuse=1 AND status='complete' ORDER BY updated DESC,id DESC LIMIT 100""",
                (identity_digest,)).fetchall()
        for row in rows:
            result = json.loads(row["payload"])
            lease = contextlib.ExitStack()
            try:
                root = contained(self.workspace, result["run_root"], exists=True)
                if self._source_active(root):
                    continue
                if not (root / ".run.lock").exists():
                    # Safe leases need the stable writer inode. Older sources
                    # remain browsable; no reader creates or mutates their lock.
                    continue
                lease.enter_context(self._source_lease(root))
                if self._source_active(root):
                    lease.close()
                    continue
                with self.connect() as connection:
                    artifacts = connection.execute("SELECT path,sha256,bytes FROM artifacts WHERE result_id=?", (row["id"],)).fetchall()
                if not artifacts:
                    raise ValueError("Required artifact manifest is empty")
                for artifact in artifacts:
                    path = contained(root, artifact["path"], exists=True)
                    if not path.is_file() or path.stat().st_size != artifact["bytes"] or provenance.file_digest(path) != artifact["sha256"]:
                        raise ValueError("Required artifact content changed")
                # Prevent a stale finalized index hit after the source has
                # been resumed or updated to a failed terminal state.
                reader = ResultReader(root)
                if reader.summary().get("complete") is not True:
                    raise ValueError("Source execution is no longer complete")
                identity = self.get_identity(identity_digest)
                if result["retention"] == "minimal" and (not reader.retained or _retained_input_issues(root, reader.retained, identity)):
                    raise ValueError("Retained inputs differ from the original execution identity")
                if result["retention"] == "full":
                    if not _scientific_config_matches(reader.config, identity):
                        raise ValueError("Source scientific configuration changed")
                    if not _input_files_intact(reader, identity):
                        raise ValueError("Source input/review snapshots changed")
                with self.connect() as connection:
                    current = connection.execute("SELECT updated,payload FROM results WHERE id=? AND can_reuse=1 AND status='complete'",
                                                 (row["id"],)).fetchone()
                if not current or current["updated"] != row["updated"] or current["payload"] != row["payload"]:
                    # A newer register/flush owns its valid generation. Do not
                    # invalidate it on behalf of a stale lookup attempt.
                    lease.close()
                    continue
            except BlockingIOError:
                lease.close()
                continue
            except sqlite3.Error:
                lease.close()
                # Unknown job state/lock contention cannot establish inactivity.
                continue
            except (KeyError, ValueError, TypeError, OSError):
                lease.close()
                result["can_reuse"] = False
                result["reuse_reasons"] = result.get("reuse_reasons", []) + ["Preserved artifact verification failed"]
                with self.connect() as connection:
                    connection.execute("UPDATE results SET can_reuse=0,payload=? WHERE id=? AND updated=? AND payload=?",
                                       (_json(result), row["id"], row["updated"], row["payload"]))
                continue
            # Keep caller exceptions outside verification's catch block.
            with lease:
                yield result
            return
        yield None

    def find_reusable(self, identity_digest):
        with self.reuse_lease(identity_digest) as result:
            return result
