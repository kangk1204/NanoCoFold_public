"""Audited parent selection and balanced, CDR-only design refinement libraries."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from . import cascade, mature, provenance
from .config import cfg_get, dump_resolved, resolve_path
from .utils import pdb_io
from .utils.shell import ToolError

SCHEMA_VERSION = 1
ENGINES = ("rfantibody", "boltzgen")
QUALITY_FIELDS = ("initial_engine_rank", "generation_rank", "rmsd", "rf2_pae", "rf2_plddt",
                  "generation_status", "generation_error", "error", "binder_chain", "pred_dir")


def enabled(cfg):
    return bool(cfg_get(cfg, "design.refinement.enabled", False))


def public_msa_template_sequences(cfg):
    """Resolve actual scaffold sequences without fetching or substituting one."""
    engines = settings(cfg)["engines"]
    sources = {}
    for engine in engines:
        value = (cfg_get(cfg, "design.boltzgen.scaffold") or cfg_get(cfg, "design.framework")) if engine == "boltzgen" else cfg_get(cfg, "design.framework")
        path = resolve_path(cfg, value)
        if not path or not path.is_file():
            raise ToolError(f"공개 {engine} scaffold 구조가 없습니다. 실제 design.framework/scaffold를 설치하세요.")
        requested_chain = cfg_get(cfg, "design.boltzgen.scaffold_chain") if engine == "boltzgen" else None
        structure = pdb_io.load_structure(path)
        chains = [(chain.id, pdb_io.chain_sequence(chain)) for chain in next(structure.get_models())]
        chains = [(identifier, sequence) for identifier, sequence in chains if sequence]
        if requested_chain:
            chains = [(identifier, sequence) for identifier, sequence in chains if identifier == requested_chain]
        if len(chains) != 1:
            raise ToolError(f"{engine} 공개 scaffold 체인이 모호하거나 없습니다: {path.name}. 단일 VHH 구조를 지정하세요.")
        sequence = chains[0][1]
        identifier = "public_scaffold_" + hashlib.sha256(sequence.encode()).hexdigest()[:16]
        sources[identifier] = {"sequence": sequence, "structure": str(path), "chain": chains[0][0]}
    return sources


def public_msa_templates(cfg, run_dir):
    """Validate and use cached public scaffolds; never search generated parents."""
    from .colabfold_msa import cached_monomer
    sources = public_msa_template_sequences(cfg)
    cache = resolve_path(cfg, cfg_get(cfg, "screen.colabfold.msa_cache_dir", "msa/monomer_cache"))
    alignments = {}
    for identifier, source in sources.items():
        try:
            alignments[identifier] = cached_monomer(cache, source["sequence"])
        except ValueError as exc:
            raise ToolError(f"공개 scaffold MSA 캐시가 없거나 손상되었습니다: {identifier}. 환경 화면에서 공개 MSA를 준비하세요. ({exc})") from exc
    annotations = mature.resolve_cdr_annotations_batch(cfg, {identifier: source["sequence"] for identifier, source in sources.items()},
                                                      Path(run_dir) / "01_msa/public_scaffold_annotation")
    templates = []
    for identifier, source in sources.items():
        entry = annotations.get(identifier, {})
        if not entry.get("valid", False):
            raise ToolError(f"공개 scaffold ANARCI 검증 실패: {identifier}: {entry.get('reason', 'numbering_unavailable')}")
        templates.append({"id": identifier, "a3m": alignments[identifier]["a3m"], "sha256": alignments[identifier]["sha256"],
                          "cdr_ranges": entry["cdr_ranges"]})
    return templates


def settings(cfg):
    opts = cfg_get(cfg, "design.refinement", {}) or {}
    result = {name: opts.get(name, default) for name, default in
              (("library_size", 1000), ("parents_per_engine", 2), ("min_mutations", 1),
               ("max_mutations", 5), ("final_count", 100))}
    for name in ("library_size", "parents_per_engine", "min_mutations", "max_mutations", "final_count"):
        if type(result[name]) is not int or result[name] < 1:
            raise ValueError(f"design.refinement.{name}는 양의 정수여야 합니다.")
    if not result["min_mutations"] <= result["max_mutations"] <= 50 or result["library_size"] > 10000:
        raise ValueError("design.refinement 변이 개수 또는 라이브러리 크기가 지원 범위를 벗어났습니다.")
    engine = cfg_get(cfg, "design.engine", "rfantibody")
    result["engines"] = list(ENGINES if engine == "both" else (engine,))
    if any(engine not in ENGINES for engine in result["engines"]):
        raise ValueError("design.engine은 rfantibody/boltzgen/both 중 하나여야 합니다.")
    controls = result["parents_per_engine"] * len(result["engines"])
    if result["library_size"] < controls or not controls < result["final_count"] <= result["library_size"]:
        raise ValueError("design.refinement.final_count는 전체 부모 수보다 크고 library_size 이하여야 합니다.")
    result["seed"] = cfg_get(cfg, "run.seed", 42)
    if type(result["seed"]) is not int or not 0 <= result["seed"] < 2**32:
        raise ValueError("design.refinement에는 0–4294967295 정수 run.seed가 필요합니다.")
    result["exclude_cys"] = bool(cfg_get(cfg, "mature.exclude.cys_substitution", True))
    result["protect_wt_cys"] = bool(cfg_get(cfg, "mature.exclude.wt_cys", True))
    result["mock"] = bool(cfg_get(cfg, "run.mock", False))
    return result


def _present(value):
    return value is not None and not pd.isna(value) and str(value).strip() not in {"", "nan", "None"}


def _json_value(value):
    return value if _present(value) else None


def _source_pool(result, cfg, run_dir):
    frame = result.get("all_candidates")
    if not isinstance(frame, pd.DataFrame):
        path = Path(run_dir) / "02_design/all_generated_candidates.csv"
        frame = pd.read_csv(path, dtype={"design_id": str}) if path.is_file() else result["candidates"]
    frame = frame.copy()
    if "design_id" not in frame or "sequence" not in frame:
        raise ValueError("초기 설계 결과에 design_id/sequence가 없습니다.")
    frame["design_id"] = frame["design_id"].astype(str)
    if frame["design_id"].duplicated().any():
        raise ValueError("초기 설계 후보 ID가 중복되었습니다.")
    if "parent_engine" not in frame:
        engine = cfg_get(cfg, "design.engine", "rfantibody")
        if engine == "both":
            raise ValueError("both 초기 설계 결과에는 후보별 parent_engine이 필요합니다.")
        frame["parent_engine"] = engine
    return frame.sort_values("design_id", kind="stable").reset_index(drop=True)


def _inputs(frame, cfg, opts):
    records = []
    for row in frame.to_dict("records"):
        record = {key: _json_value(row.get(key)) for key in ("design_id", "sequence", "parent_engine", *QUALITY_FIELDS)}
        path = Path(str(record["pred_dir"])) if record["pred_dir"] else None
        record["initial_structure_sha256"] = (provenance.file_digest(path) if path and path.is_file() else
                                               provenance.directory_identity(path)["sha256"] if path and path.is_dir() else None)
        records.append(record)
    return {"schema_version": SCHEMA_VERSION, "settings": opts, "parents": records,
            "anarci": cfg_get(cfg, "mature.anarci", {}), "cdr_scheme": cfg_get(cfg, "mature.cdr.scheme", "imgt"),
            "mock_cdr_regions": cfg_get(cfg, "mature.cdr.regions", {}) if opts["mock"] else None,
            "numpy_version": mature.np.__version__,
            "generator_sha256": provenance.file_digest(Path(mature.__file__)),
            "refinement_sha256": provenance.file_digest(Path(__file__))}


def _quality(row):
    # Each engine chooses its own representatives. No sum or normalization
    # makes a BoltzGen score comparable to an RFantibody score.
    keys = ("initial_engine_rank", "generation_rank", "rmsd", "rf2_pae") if row["parent_engine"] == "rfantibody" else ("initial_engine_rank", "generation_rank")
    values = []
    for key in keys:
        try:
            value = float(row.get(key))
            values.append(value if math.isfinite(value) else math.inf)
        except (TypeError, ValueError):
            values.append(math.inf)
    return (*values, row["design_id"])


def _annotations(frame, cfg, phase, opts):
    sequences = {row["design_id"]: row["sequence"] for row in frame.to_dict("records")
                 if isinstance(row["sequence"], str) and row["sequence"] and not set(row["sequence"]) - set(mature.AMINO_ACIDS)}
    if opts["mock"]:
        ranges = cfg_get(cfg, "mature.cdr.regions", {}) or {}
        return {identifier: {"valid": True, "reason": None, "cdr_ranges": ranges,
                             "validation_kind": "mock", "anarci_validated": False}
                for identifier in sequences}
    return mature.resolve_cdr_annotations_batch(cfg, sequences, phase / "anarci")


def _structure_reason(row, mock):
    if mock:
        return None
    path = Path(str(row.get("pred_dir"))) if _present(row.get("pred_dir")) else None
    if not path or not path.is_file():
        return "initial_structure_missing"
    try:
        chain = str(row.get("binder_chain") or "B")
        structure = pdb_io.load_structure(path)
        actual = pdb_io.chain_sequence(pdb_io.get_chain(structure, chain))
    except Exception:
        return "initial_structure_binder_unreadable"
    return None if actual == row["sequence"] else "initial_structure_sequence_mismatch"


def _choose_parents(frame, cfg, phase, opts):
    annotations = _annotations(frame, cfg, phase, opts)
    audit, valid = [], []
    for row in frame.to_dict("records"):
        identifier, sequence = row["design_id"], row["sequence"]
        annotation = annotations.get(identifier, {})
        reason = None
        if row["parent_engine"] not in opts["engines"]:
            reason = "engine_not_requested"
        elif not isinstance(sequence, str) or not sequence or set(sequence) - set(mature.AMINO_ACIDS):
            reason = "noncanonical_parent_sequence"
        elif any(_present(row.get(key)) for key in ("generation_error", "error")) or row.get("generation_status") in {"failed", "cancelled", "interrupted"}:
            reason = "initial_generation_failed"
        elif not annotation.get("valid", False):
            reason = annotation.get("reason") or "anarci_validation_unavailable"
        else:
            reason = _structure_reason(row, opts["mock"])
        ranges = annotation.get("cdr_ranges", {})
        if reason is None:
            try:
                if set(ranges) != {"CDR1", "CDR2", "CDR3"}:
                    raise ValueError("CDR1/2/3가 모두 필요합니다.")
                mature.generate_random_library(sequence, ranges, size=2, min_mutations=opts["min_mutations"],
                                               max_mutations=opts["max_mutations"], seed=opts["seed"],
                                               exclude_cys=opts["exclude_cys"], protect_wt_cys=opts["protect_wt_cys"])
            except ValueError:
                reason = "invalid_or_unmutable_cdr_ranges"
        entry = {**row, "parent_valid": reason is None, "selected_parent": False,
                 "parent_selection_reason": reason, "validation_kind": annotation.get("validation_kind"),
                 "anarci_validated": bool(annotation.get("anarci_validated", False)),
                 "cdr_ranges": json.dumps(ranges, sort_keys=True)}
        audit.append(entry)
        if reason is None:
            valid.append(entry)
    selected, seen = [], set()
    for engine in opts["engines"]:
        count = 0
        for row in sorted((item for item in valid if item["parent_engine"] == engine), key=_quality):
            if row["sequence"] in seen:
                row["parent_selection_reason"] = "duplicate_parent_sequence"
                continue
            if count >= opts["parents_per_engine"]:
                row["parent_selection_reason"] = "outside_parent_budget"
                continue
            row["selected_parent"] = True
            row["parent_selection_reason"] = "within_engine_parent_selection"
            selected.append(row)
            seen.add(row["sequence"])
            count += 1
    audit_frame = pd.DataFrame(audit)
    audit_frame.to_csv(phase / "parent_audit.csv", index=False)
    missing = {engine: opts["parents_per_engine"] - sum(row["parent_engine"] == engine for row in selected)
               for engine in opts["engines"]}
    if any(missing.values()):
        raise ToolError(f"검증된 고유 VHH 부모가 부족합니다: {missing}. parent_audit.csv에서 제외 이유를 확인하세요.")
    return pd.DataFrame(selected), audit_frame


def _split(total, count):
    return [total // count + (index < total % count) for index in range(count)]


def _library(parents, opts):
    quotas = {}
    for engine, engine_total in zip(opts["engines"], _split(opts["library_size"], len(opts["engines"]))):
        identifiers = parents.loc[parents["parent_engine"].eq(engine), "design_id"].tolist()
        quotas.update(zip(identifiers, _split(engine_total, len(identifiers))))
    seen = set(parents["sequence"])
    records = []
    for parent in parents.to_dict("records"):
        identifier, sequence = parent["design_id"], parent["sequence"]
        ranges = json.loads(parent["cdr_ranges"])
        seed = int.from_bytes(hashlib.sha256(f"{opts['seed']}:{identifier}".encode()).digest()[:4], "big")
        quota = quotas[identifier]
        picked = [{"variant_id": "WT", "sequence": sequence, "mutation": "parent_control", "mutation_count": 0,
                   "mutation_positions": "[]", "region": "parent_control", "position": -1, "wt_aa": "-", "mut_aa": "-",
                   "library_strategy": "random", "library_seed": seed}]
        if quota > 1:
            size = max(2, quota)
            while True:
                drawn = mature.generate_random_library(sequence, ranges, size=size, min_mutations=opts["min_mutations"],
                                                       max_mutations=opts["max_mutations"], seed=seed,
                                                       exclude_cys=opts["exclude_cys"], protect_wt_cys=opts["protect_wt_cys"])
                picked = [picked[0]]
                local = set()
                for row in drawn.iloc[1:].to_dict("records"):
                    if row["sequence"] not in seen:
                        picked.append(row)
                        local.add(row["sequence"])
                    if len(picked) == quota:
                        break
                if len(picked) == quota:
                    seen.update(local)
                    break
                capacity = min(100000, drawn.attrs["library_generation"]["capacity_including_wt"])
                if size >= capacity:
                    raise ToolError(f"{identifier}: 다른 부모와 겹치지 않는 CDR 변이 공간이 부족합니다.")
                size = min(capacity, max(size + quota - len(picked), size * 2))
        for index, row in enumerate(picked):
            control = index == 0
            records.append({**row, "design_id": identifier if control else f"{identifier}__RND_{index:06d}",
                            "parent_id": identifier, "parent_engine": parent["parent_engine"], "parent_sequence": sequence,
                            "parent_initial_structure": parent.get("pred_dir"), "is_parent_control": control,
                            "source": "design_refinement", "cdr_ranges": parent["cdr_ranges"],
                            "parent_validation_kind": parent["validation_kind"], "parent_anarci_validated": parent["anarci_validated"]})
    frame = pd.DataFrame(records).drop(columns=["variant_id"])
    if len(frame) != opts["library_size"] or frame["sequence"].duplicated().any() or frame["design_id"].duplicated().any():
        raise ValueError("refinement 라이브러리 크기/고유성 검증 실패")
    return frame


def build_library(cfg, run_dir, result):
    """Freeze every parent decision and library; resume only a matching manifest."""
    opts = settings(cfg)
    frame = _source_pool(result, cfg, run_dir)
    phase = Path(run_dir) / "02_design/refinement"
    phase.mkdir(parents=True, exist_ok=True)
    identity = _inputs(frame, cfg, opts)
    fingerprint = provenance.fingerprint(identity)
    manifest_path = phase / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("fingerprint") != fingerprint:
            raise ValueError("refinement 입력/설정/생성기가 변경되었습니다. 기존 결과를 보존하고 새 실행을 시작하세요.")
        for name in ("parents.csv", "parent_audit.csv", "library.csv"):
            if not (phase / name).is_file() or provenance.file_digest(phase / name) != manifest.get("outputs", {}).get(name):
                raise ValueError(f"refinement 저장 결과의 해시가 손상되었습니다: {name}")
        library = pd.read_csv(phase / "library.csv", dtype={"design_id": str, "parent_id": str})
        return library, {**manifest["summary"], "resumed": True}
    provenance.archive_existing([phase / name for name in ("parents.csv", "parent_audit.csv", "library.csv", "failure.json")], phase)
    try:
        parents, audit = _choose_parents(frame, cfg, phase, opts)
        parents.to_csv(phase / "parents.csv", index=False)
        library = _library(parents, opts)
        library.to_csv(phase / "library.csv", index=False)
    except (ValueError, ToolError) as exc:
        (phase / "failure.json").write_text(json.dumps({"schema_version": SCHEMA_VERSION, "fingerprint": fingerprint,
                                                       "settings": opts, "error": str(exc)}, ensure_ascii=False, indent=2) + "\n")
        raise
    summary = {"schema_version": SCHEMA_VERSION, "library_size": len(library), "parent_controls": len(parents),
               "initial_generated": len(frame), "valid_initial_parents": int(audit["parent_valid"].sum()),
               "parents_per_engine": parents.groupby("parent_engine").size().to_dict(),
               "library_per_engine": library.groupby("parent_engine").size().to_dict(),
               "library_per_parent": library.groupby("parent_id").size().to_dict(),
               "final_count": opts["final_count"], "top_n_mutants": opts["final_count"] - len(parents),
               "parent_selection": "independent_within_engine_quality_then_id", "mock": opts["mock"],
               "anarci_validated": bool(parents["anarci_validated"].all()),
               "mutation_count": [opts["min_mutations"], opts["max_mutations"]], "seed": opts["seed"],
               "sampling": "uniform_nonexhausted_mutation_count_then_positions_and_alternative_aa_with_global_duplicate_rejection",
               "audit": "02_design/refinement/parent_audit.csv", "resumed": False}
    manifest = {"schema_version": SCHEMA_VERSION, "fingerprint": fingerprint, "inputs": identity, "summary": summary,
                "outputs": {name: provenance.file_digest(phase / name) for name in ("parents.csv", "parent_audit.csv", "library.csv")}}
    temporary = manifest_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(manifest_path)
    return library, summary


def run(cfg, run_dir, state, target, result, *, predict, score, finalize):
    cfg = copy.deepcopy(cfg)
    if cfg_get(cfg, "screen.engine") != "colabfold" or not cascade.enabled(cfg):
        raise ValueError("design.refinement에는 screen.engine=colabfold와 screen.cascade.enabled=true가 필요합니다.")
    if hasattr(state, "begin_phase"):
        state.begin_phase("design_refinement")
    library, summary = build_library(cfg, run_dir, result)
    cfg["screen"]["cascade"]["final_count"] = summary["final_count"]
    cfg = cascade.budget_config(cfg, library)
    dump_resolved(cfg, Path(run_dir) / "run_config.yaml")
    stage = Path(run_dir) / "02_screen"
    stage.mkdir(parents=True, exist_ok=True)
    library.to_csv(stage / "candidates.csv", index=False)
    state.set_phase("design_refinement", summary)
    print(f"[design refinement] 부모 {summary['parent_controls']}종 → CDR 라이브러리 {len(library)}종 → Boltz-2 {summary['final_count']}종")
    return cascade.run(cfg, Path(run_dir), state, target, library, None, predict=predict, score=score, finalize=finalize)
