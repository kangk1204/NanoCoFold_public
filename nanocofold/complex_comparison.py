"""Ordered protein-chain comparison; no VHH assumptions or score aggregation."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pandas as pd

from . import colabfold, engine_comparison, provenance, screen
from .config import cfg_get, resolve_path, dump_resolved
from .utils import shell
from .utils.atomic import write_json


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
    """Freeze all protein chains directly; no VHH candidate or parent projection."""
    from .colabfold_msa import cached_monomer, read_monomer, read_multimer, _unique_chains, _query_descriptor
    applied, root = copy.deepcopy(cfg), Path(root)
    records = {p['id']: p['sequence'] for p in entries}
    queries = {'chain::'+p['id']:p['sequence'] for p in entries}
    source = cfg_get(applied, 'screen.colabfold.msa_source', 'cache')
    acquired = {}
    if source == 'local_mmseqs2':
        acquired = engine_comparison._acquire_reviewed_local(applied, root, state, queries)
    elif source == 'public_api':
        acquired = engine_comparison._acquire_reviewed_api(applied, root, state, queries)
    elif source != 'cache':
        raise ValueError('Complex MSA source must be cache, local_mmseqs2, or public_api')
    cache = resolve_path(applied, cfg_get(applied,'screen.colabfold.msa_cache_dir','msa/monomer_cache'))
    monomers = {}
    for chain,sequence in records.items():
        try:
            monomers[chain] = acquired.get(sequence) or cached_monomer(cache, sequence)
        except ValueError as exc:
            raise shell.ToolError(f'복합체 {chain}의 정확한 단량체 MSA가 없습니다: {exc}') from exc
    maximum = cfg_get(applied,'screen.colabfold.msa_max_sequences',2048)
    sequences,cardinality = _unique_chains(records)
    alignments = tuple(read_monomer(next(v['a3m'] for c,v in monomers.items() if records[c]==s),s,maximum) for s in sequences)
    query = _query_descriptor(sequences,cardinality,alignments,{'mode':'precomputed','role':'whole_protein_complex'})
    folder=root/'01_msa/comparison';folder.mkdir(parents=True,exist_ok=True)
    manifest = {'schema_version':1,'pairing':'unpaired','static_chains':{},'parents':{},
        'queries':{'complex_input':{'chains':records,'a3m':'complex_input.a3m','sha256':query.identity['query_a3m_sha256']}},
        'role':'whole_protein_complex','candidate_selection_applicable':False,
        'protein_chains':{c:{'sequence':records[c],'source_monomer_sha256':v['sha256']} for c,v in monomers.items()}}
    path=folder/'manifest.json'
    if path.exists() and json.loads(path.read_text())!=manifest:
        raise ValueError('Complex MSA inputs changed; preserve this run and use a new directory')
    query_path=folder/'complex_input.a3m'
    if query_path.exists():
        read_multimer(query_path,records,expected_sha256=query.identity['query_a3m_sha256'],max_sequences=maximum)
    else:query.write(query_path)
    if not path.exists():write_json(path,manifest,root=root)
    applied.setdefault('screen',{}).setdefault('colabfold',{}).update(msa_mode='precomputed',msa_dir=str(folder.resolve()),msa_manifest_sha256=provenance.file_digest(path))
    dump_resolved(applied,root/'run_config.yaml')
    if state is not None:
        origins=[v['receipt'].get('source_provenance',{}) for v in monomers.values()]
        uploaded=any(v.get('explicit_sequence_upload_authorized') is True for v in origins)
        receipt_path=root/'01_msa/api_comparison_acquisition.json'
        api=json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}
        state.set_phase('comparison_msa',{'mode':'exact_cached_protein_monomers','pairing':'unpaired','provider':source,
            'chains':len(records),'unique_sequences':len(sequences),'candidate_selection_applicable':False,
            'manifest':str(path.relative_to(root)),'manifest_sha256':provenance.file_digest(path),
            'private_sequences_submitted_to_public_api':uploaded,
            'public_api_requests':api.get('public_api_requests',0) if not uploaded or api else None,
            'request_counter_scope':api.get('request_counter_scope','this_run; source cache history unspecified'),
            'source_providers':sorted({v.get('provider','unspecified') for v in origins})})
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
