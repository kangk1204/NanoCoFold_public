"""Ordered protein-chain comparison; no VHH assumptions or score aggregation."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pandas as pd

from . import colabfold, engine_comparison, provenance, screen
from .config import cfg_get


def configure(cfg, settings):
    """Translate reviewed complex options without acquiring MSA or running tools."""
    if not settings.get("comparison_engines"):
        return cfg
    cfg.setdefault("screen", {}).update(restraint={"enabled": False}, allow_public_msa_server=settings["allow_public_msa_server"])
    cfg["screen"].setdefault("colabfold", {}).update(
        msa_mode="precomputed", msa_source=settings["msa_source"],
        num_models=settings["models_per_seed"], num_seeds=1, num_recycles=settings["recycles"], batch_size=1)
    cfg["screen"].setdefault("opendde", {}).update(
        enabled="opendde" in settings["comparison_engines"], cycles=settings["recycles"],
        steps=settings["diffusion_steps"], samples=settings["models_per_seed"])
    return cfg


def effective_config(cfg, settings, engine, seed):
    """One explicit option mapping for single and comparison dispatch."""
    applied = copy.deepcopy(cfg)
    applied.setdefault("run", {})["seed"] = seed
    score = applied.setdefault("score", {})
    score.setdefault("boltz2", {})["seed"] = seed
    score.update(boltz2_diffusion_samples=settings["models_per_seed"],
                 boltz2_recycling_steps=settings["recycles"],
                 boltz2_extra_args=["--no_kernels", "--num_workers", "0", "--sampling_steps", str(settings["diffusion_steps"])])
    screen_cfg = applied.setdefault("screen", {})
    screen_cfg.update(num_trunk_recycles=settings["recycles"],
                      num_diffn_timesteps=settings["diffusion_steps"], num_diffn_samples=1,
                      msa_mode="single", restraint={"enabled": False})
    screen_cfg.setdefault("colabfold", {}).update(num_models=settings["models_per_seed"], num_seeds=1,
                                                 num_recycles=settings["recycles"], batch_size=1)
    screen_cfg.setdefault("opendde", {}).update(cycles=settings["recycles"], steps=settings["diffusion_steps"],
                                              samples=settings["models_per_seed"])
    if engine == "chai1" and settings.get("comparison_engines"):
        screen_cfg["msa_mode"] = "precomputed"
    return applied


def condition_receipt(cfg, settings, engine):
    """Requested options and effective adapter conditions are separate facts."""
    conditions = engine_comparison._conditions(cfg, engine)
    if not settings.get("comparison_engines"):
        conditions["msa"] = "empty"
    return {"requested": {key: settings[key] for key in ("recycles", "diffusion_steps", "models_per_seed", "msa", "restraints")},
            "effective": conditions,
            "option_mapping": {"recycles": "cycles" if engine == "opendde" else "num_recycles",
                               "diffusion_steps": "steps" if engine == "opendde" else "diffusion_timesteps" if engine == "chai1" else "--sampling_steps" if engine == "boltz2" else None,
                               "models_per_seed": "samples" if engine == "opendde" else "diffusion_samples" if engine in {"chai1", "boltz2"} else "num_models"},
            "unsupported_options": ["diffusion_steps: not applicable to ColabFold"] if engine == "colabfold" else []}


def prepare(cfg, root, state, entries):
    """Reuse the frozen monomer provider and its receipts for every ordered chain.

    The last chain is an exact parent solely for the existing MSA manifest format.
    No mutation, CDR projection, sequence truncation, or candidate selection occurs.
    Homomer copies share one sequence cache while retaining their chain axes.
    """
    target = {"sequence": entries[0]["sequence"], "aux_chains": entries[1:-1]}
    candidate = pd.DataFrame([{"design_id": "complex_input", "sequence": entries[-1]["sequence"]}])
    applied, _, alignments = engine_comparison.prepare_msa(cfg, root, state, target, candidate)
    monomers = {p["id"]: alignments["candidate::complex_input" if p is entries[-1] else "chain::" + p["id"]] for p in entries}
    return applied, monomers


def predict(cfg, folder, records, engine, monomers):
    from .complex import validate_settings
    options = validate_settings(cfg_get(cfg, "complex", {}))
    applied = effective_config(cfg, options, engine, cfg_get(cfg, "run.seed", 42))
    paths = {letter: entry["a3m"] for letter, entry in monomers.items()}
    binder = next(reversed(records))
    target = {"sequence": records["A"], "chain_msas": paths, "target_msa": paths["A"], "binder_msa": paths[binder]}
    if engine == "colabfold":
        candidate = pd.DataFrame([{"design_id": "complex_input", "sequence": records[binder], "parent_id": "complex_input"}])
        result = colabfold.predict(applied, folder, target, candidate, chain_records=records)
        error = result.iloc[0].get("error")
        if pd.notna(error):
            raise ValueError(str(error))
        directory = Path(result.iloc[0]["pred_dir"])
    elif engine == "boltz2":
        screen._predict_boltz2(applied, folder, records, paths["A"], binder, [], False,
                               binder_msa=paths[binder], chain_msas=paths)
        directory = next((folder / "boltz_out").glob("boltz_results_*/predictions/*"))
    elif engine == "chai1":
        directory = screen.prepare_chai_precomputed_msa(applied, folder / "chai_msa", records, monomers)
        applied["screen"].update(msa_mode="precomputed")
        applied["screen"].setdefault("chai1", {})["msa_directory"] = str(directory)
        screen._predict_chai1(applied, folder, target, records, binder, [], False)
        directory = folder / "chai_out"
    elif engine == "opendde":
        from .opendde import predict as opendde_predict
        directory = opendde_predict(applied, folder, records, chain_msas=paths)
    else:
        raise ValueError("지원하지 않는 복합체 비교 엔진")
    sources = {letter: {"sequence_sha256": hashlib.sha256(records[letter].encode()).hexdigest(),
                        "source_monomer_sha256": entry["sha256"]} for letter, entry in monomers.items()}
    inputs = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.suffix in {".a3m", ".pqt", ".yaml", ".fasta", ".csv", ".log"}:
            inputs[str(path.relative_to(folder))] = {"bytes": path.stat().st_size, "sha256": provenance.file_digest(path)}
    return directory, {"source_monomers": sources, "actual_input_artifacts": inputs,
                       "engine_conditions": engine_comparison._conditions(applied, engine),
                       "condition_receipt": condition_receipt(applied, options, engine),
                       "native_completion_receipts": {str(p.relative_to(folder)): provenance.file_digest(p)
                           for p in sorted(folder.rglob("*complete*.json")) if p.is_file()}}
