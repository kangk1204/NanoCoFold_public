"""Pinned BoltzGen nanobody design adapter; preserves raw generation records."""
from __future__ import annotations

import copy
import json
import re
import string
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from Bio.PDB import MMCIFIO, MMCIF2Dict
from Bio.PDB.Chain import Chain
from Bio.PDB.Model import Model
from Bio.PDB.Structure import Structure

from . import provenance
from .config import cfg_get, resolve_path
from .utils import pdb_io, shell
from .utils.atomic import write_csv

VERSION = "0.3.2"
SOURCE_COMMIT = "31d9d9b9c72245b4ed6fe8742d6fbf4e1a3552a0"
MODEL_NAMES = ("boltzgen1_diverse.ckpt", "boltzgen1_adherence.ckpt", "boltzgen1_ifold.ckpt", "boltz2_conf_final.ckpt", "mols.zip")
BASE_COLUMNS = ["design_id", "sequence", "source", "parent_engine", "pred_dir", "cif_path", "target_chain", "binder_chain", "generation_status", "generation_error", "tier2_requested", "structure_stage"]


def settings(cfg: dict) -> dict:
    """A bounded CLI contract; arbitrary upstream Hydra/command options are excluded."""
    value = cfg_get(cfg, "design.boltzgen", {})
    if not isinstance(value, dict):
        raise ValueError("design.boltzgen must be an object")
    if value.get("extra_args"):
        raise ValueError("BoltzGen arbitrary extra_args are unsupported; use named settings")
    result = dict(value)
    for name, default, low, high in (("diffusion_batch_size", 1, 1, 32), ("inverse_fold_num_sequences", 1, 1, 16),
                                   ("num_workers", 1, 0, 16), ("sampling_steps", 500, 1, 1000),
                                   ("recycling_steps", 3, 1, 20), ("folding_sampling_steps", 200, 1, 1000)):
        v = value.get(name, default)
        if type(v) is not int or not low <= v <= high:
            raise ValueError(f"design.boltzgen.{name} must be integer {low}..{high}")
        result[name] = v
    kernel = value.get("use_kernels", "false")
    if not isinstance(kernel, str) or kernel not in {"auto", "true", "false"}:
        raise ValueError("design.boltzgen.use_kernels must be auto|true|false")
    result["use_kernels"] = kernel
    if not isinstance(value.get("env_vars", {}), dict):
        raise ValueError("design.boltzgen.env_vars must be an object")
    return result


def model_paths(cfg: dict) -> dict[str, Path]:
    root = resolve_path(cfg, cfg_get(cfg, "design.boltzgen.cache_dir", "models/boltzgen"))
    return {name: root / name for name in MODEL_NAMES}


def _canonical_structure(path: Path, selections: list[tuple[str, str]], destination: Path) -> tuple[dict, dict]:
    """Write explicit canonical chain/1-based indices, retaining author mapping separately."""
    if path.resolve() == destination.resolve():
        raise ValueError("Canonical input must be separate from protected source structure")
    source = pdb_io.load_structure(path)
    structure = Structure("design_input")
    model = Model(0)
    structure.add(model)
    mapping, sequences, monomers = {}, {}, {}
    for original, canonical in selections:
        chain = pdb_io.get_chain(source, original)
        output = Chain(canonical)
        model.add(output)
        residues = list(pdb_io.iter_residues(chain))
        if not residues or any(not {"N", "CA", "C", "O"}.issubset(residue.child_dict) for residue in residues):
            raise ValueError(f"Incomplete protein coordinates in chain {original}")
        mapping[canonical] = []
        for index, residue in enumerate(residues, 1):
            mapping[canonical].append({"position": index, "original_chain": original, "original_number": residue.id[1],
                                       "insertion_code": residue.id[2].strip(), "original_label": pdb_io.residue_label(residue, original)})
            cloned = copy.deepcopy(residue)
            cloned.detach_parent()
            cloned.id = (" ", index, " ")
            output.add(cloned)
        sequences[canonical] = pdb_io.chain_sequence(output)
        monomers[canonical] = [residue.resname for residue in output]
    destination.parent.mkdir(parents=True, exist_ok=True)
    writer = MMCIFIO()
    writer.set_structure(structure)
    writer.save(str(destination))
    # BioPython normally assigns label_asym_id alphabetically, independent of
    # chain.id. BoltzGen reads label indices, so explicitly reconcile both IDs.
    document = MMCIF2Dict.MMCIF2Dict(str(destination))
    document["_atom_site.label_asym_id"] = list(document["_atom_site.auth_asym_id"])
    # BoltzGen's loader requires polymer/entity sequence loops; atom-only
    # BioPython mmCIF exports are insufficient even when coordinates parse.
    entities = {canonical: str(index) for index, (_, canonical) in enumerate(selections, 1)}
    document["_atom_site.label_entity_id"] = [entities[chain] for chain in document["_atom_site.auth_asym_id"]]
    document["_entity.id"] = list(entities.values())
    document["_entity.type"] = ["polymer"] * len(entities)
    document["_entity_poly.entity_id"] = list(entities.values())
    document["_entity_poly.type"] = ["polypeptide(L)"] * len(entities)
    document["_entity_poly.pdbx_seq_one_letter_code"] = list(sequences.values())
    document["_entity_poly.pdbx_seq_one_letter_code_can"] = list(sequences.values())
    document["_entity_poly.pdbx_strand_id"] = list(entities)
    document["_struct_asym.id"] = list(entities)
    document["_struct_asym.entity_id"] = list(entities.values())
    polymer_rows = [(entities[chain], str(index), monomer) for chain, names in monomers.items() for index, monomer in enumerate(names, 1)]
    for column, index in (("entity_id", 0), ("num", 1), ("mon_id", 2)):
        document["_entity_poly_seq." + column] = [row[index] for row in polymer_rows]
    document["_entity_poly_seq.hetero"] = ["n"] * len(polymer_rows)
    writer.set_dict(document)
    writer.save(str(destination))
    return mapping, sequences


def _cdr_ranges(cfg: dict, sequence: str, phase: Path) -> dict:
    configured = cfg_get(cfg, "design.boltzgen.cdr_regions")
    if configured:
        ranges = configured
    else:
        from .mature import resolve_cdr_ranges_batch
        ranges = resolve_cdr_ranges_batch(cfg, {"scaffold": sequence}, phase / "scaffold_annotation").get("scaffold", {})
    if not isinstance(ranges, dict) or set(ranges) != {"CDR1", "CDR2", "CDR3"}:
        raise ValueError("BoltzGen scaffold needs all three ANARCI CDRs or explicit design.boltzgen.cdr_regions")
    occupied = set()
    validated = {}
    for name in ("CDR1", "CDR2", "CDR3"):
        bounds = ranges[name]
        if not isinstance(bounds, (tuple, list)) or len(bounds) != 2 or any(type(v) is not int for v in bounds):
            raise ValueError(f"Invalid scaffold CDR bounds: {name}")
        start, end = bounds
        if not 1 <= start <= end <= len(sequence) or occupied.intersection(range(start, end + 1)):
            raise ValueError(f"Overlapping/out-of-range scaffold CDR: {name}")
        if "C" in sequence[start - 1:end]:
            raise ValueError(f"Scaffold CDR {name} includes cysteine; preserve scaffold disulfides explicitly")
        occupied.update(range(start, end + 1))
        validated[name] = [start, end]
    return validated


def prepare_specification(cfg: dict, phase: Path, target: dict) -> dict:
    structure = target.get("structure")
    if not structure:
        raise ValueError("BoltzGen requires a prepared target structure")
    primary = str(target.get("chain_id") or cfg_get(cfg, "target.chain", "A"))
    source = pdb_io.load_structure(structure)
    actual = pdb_io.chain_ids(source)
    partners = target.get("aux_chain_ids")
    if partners is None:
        partners = [chain for chain in actual if chain != primary and pdb_io.chain_sequence(pdb_io.get_chain(source, chain))]
    declared = target.get("aux_chains") or []
    if declared:
        expected_partners = sorted(entry["sequence"] for entry in declared)
        actual_partners = sorted(pdb_io.chain_sequence(pdb_io.get_chain(source, chain)) for chain in partners)
        if actual_partners != expected_partners:
            raise ValueError("Prepared target partner sequences do not match declared partners")
    if primary not in actual or any(chain not in actual for chain in partners):
        raise ValueError("Prepared target chain selection is absent from its structure")
    chain_pool = [letter for letter in string.ascii_uppercase if letter not in {"T", "H"}]
    if len(partners) > 23:
        raise ValueError("BoltzGen supports at most 24 target chains plus one nanobody")
    selections = [(primary, "T")] + list(zip(partners, chain_pool))
    inputs = phase / "inputs"
    mapping, sequences = _canonical_structure(Path(structure), selections, inputs / "target.cif")
    if target.get("sequence") and sequences["T"] != target["sequence"]:
        raise ValueError("Prepared target sequence does not match coordinates; internal gaps cannot be silently removed")
    scaffold = resolve_path(cfg, cfg_get(cfg, "design.boltzgen.scaffold") or cfg_get(cfg, "design.framework"))
    if not scaffold or not scaffold.is_file():
        raise FileNotFoundError(f"BoltzGen scaffold structure missing: {scaffold}")
    scaffold_structure = pdb_io.load_structure(scaffold)
    chain_ids = pdb_io.chain_ids(scaffold_structure)
    selected = cfg_get(cfg, "design.boltzgen.scaffold_chain")
    if not selected:
        if len(chain_ids) != 1:
            raise ValueError("Multiple scaffold chains: set design.boltzgen.scaffold_chain explicitly")
        selected = chain_ids[0]
    scaffold_mapping, scaffold_sequences = _canonical_structure(scaffold, [(selected, "H")], inputs / "scaffold.cif")
    scaffold_seq = scaffold_sequences["H"]
    ranges = _cdr_ranges(cfg, scaffold_seq, phase)
    target_entity = {"path": "target.cif", "include": [{"chain": {"id": canonical}} for _, canonical in selections]}
    hotspot_mode = cfg_get(cfg, "design.hotspot_mode", "none")
    if not isinstance(hotspot_mode, str) or hotspot_mode not in {"none", "specified"}:
        raise ValueError("design.hotspot_mode must be none|specified")
    if hotspot_mode == "specified":
        explicit = cfg_get(cfg, "target.hotspots")
        if not isinstance(explicit, list) or not explicit:
            raise ValueError("Specified hotspot mode requires a nonempty explicit target.hotspots list")
        labels = target.get("hotspot_pdb_labels") or target.get("hotspot_labels") or []
        by_label = {entry["original_label"]: entry["position"] for entry in mapping["T"]}
        if not labels or any(label not in by_label for label in labels):
            raise ValueError("Hotspots cannot be uniquely mapped to canonical target residues")
        target_entity["binding_types"] = [{"chain": {"id": "T", "binding": ",".join(str(by_label[label]) for label in labels)}}]
    # File-design keeps every framework residue fixed, redesigning only CDRs.
    cdr_indices = ",".join(f"{s}..{e}" for s, e in ranges.values())
    specification = {"entities": [{"file": target_entity}, {"file": {"path": "scaffold.cif", "include": [{"chain": {"id": "H"}}],
                     "structure_groups": [{"group": {"id": "H", "visibility": 2}},
                                          {"group": {"id": "H", "visibility": 0, "res_index": cdr_indices}}],
                     "design": [{"chain": {"id": "H", "res_index": cdr_indices}}]}}]}
    spec_path = inputs / "nanobody.yaml"
    spec_path.write_text(yaml.safe_dump(specification, sort_keys=False))
    contract = {"target_sequences": sequences, "scaffold_sequence": scaffold_seq, "cdr_regions": ranges,
                "target_chain": "T", "binder_chain": "H", "residue_mapping": mapping, "scaffold_mapping": scaffold_mapping,
                "specification": str(spec_path), "hotspot_mode": hotspot_mode}
    (inputs / "residue_mapping.json").write_text(json.dumps(contract, indent=2) + "\n")
    return contract


def command(cfg: dict, phase: Path, specification: Path, *, dry_run=False) -> list[str]:
    opts = settings(cfg)
    count = cfg_get(cfg, "design.num_designs", 2)
    if type(count) is not int or not 1 <= count <= 10000:
        raise ValueError("design.num_designs must be integer 1..10000 per engine")
    env = resolve_path(cfg, opts.get("env", "envs/boltzgen"))
    paths = model_paths(cfg)
    argv = [str(shell.env_executable(env, "boltzgen", dry_run=dry_run)), "run", str(specification), "--output", str(phase / "raw"),
            "--protocol", "nanobody-anything", "--num_designs", str(count), "--budget", str(count), "--devices", "1",
            "--num_workers", str(opts["num_workers"]), "--diffusion_batch_size", str(opts["diffusion_batch_size"]),
            "--inverse_fold_num_sequences", str(opts["inverse_fold_num_sequences"]), "--use_kernels", opts["use_kernels"],
            "--cache", str(paths["mols.zip"].parent), "--moldir", str(paths["mols.zip"]),
            "--design_checkpoints", str(paths["boltzgen1_diverse.ckpt"]), str(paths["boltzgen1_adherence.ckpt"]),
            "--inverse_fold_checkpoint", str(paths["boltzgen1_ifold.ckpt"]), "--folding_checkpoint", str(paths["boltz2_conf_final.ckpt"]),
            "--steps", "design", "inverse_folding", "folding", "analysis",
            "--config", "design", f"sampling_steps={opts['sampling_steps']}", f"recycling_steps={opts['recycling_steps']}",
            "--config", "folding", f"sampling_steps={opts['folding_sampling_steps']}", f"recycling_steps={opts['recycling_steps']}"]
    # Initial designs are all retained for independent parent selection. No
    # affinity or hard filtering step is needed before the common cascade.
    return argv


def _reconcile_refold(model: Path, phase: Path, contract: dict, recorded) -> Path:
    """Refold writers rename chains alphabetically; resolve exact sequences first."""
    structure = pdb_io.load_structure(model)
    actual = {chain: pdb_io.chain_sequence(pdb_io.get_chain(structure, chain))
              for chain in pdb_io.chain_ids(structure)}
    expected = dict(contract["target_sequences"])
    fixed = contract["scaffold_sequence"]
    variable = {i for start, end in contract["cdr_regions"].values() for i in range(start - 1, end)}
    def binder_matches(sequence):
        return (len(sequence) == len(fixed) and not re.search("[^ACDEFGHIKLMNPQRSTVWY]", sequence)
                and all(sequence[i] == fixed[i] for i in range(len(fixed)) if i not in variable)
                and (not isinstance(recorded, str) or sequence == recorded))
    if set(actual) == {*expected, "H"}:
        # Existing logical IDs remain authoritative; downstream validation will
        # expose a sequence mismatch instead of reassigning a damaged chain.
        return model
    if len(actual) != len(expected) + 1:
        raise ValueError("Unexpected number of refold chains")
    mapping, used = {}, set()
    for logical, sequence in expected.items():
        choices = [chain for chain, seq in actual.items() if seq == sequence and chain not in used]
        if len(choices) != 1:
            raise ValueError(f"Ambiguous/missing refold chain mapping for {logical}")
        mapping[logical] = choices[0]
        used.add(choices[0])
    choices = [chain for chain, seq in actual.items() if chain not in used and binder_matches(seq)]
    if len(choices) != 1:
        raise ValueError("Ambiguous/missing refold binder sequence mapping")
    mapping["H"] = choices[0]
    if any(not np.isfinite(atom.coord).all() for atom in structure.get_atoms()):
        raise ValueError("Nonfinite generated coordinates")
    destination = phase / "normalized_refolds" / model.name
    receipt_path = destination.with_suffix(".mapping.json")
    source_hash = provenance.file_digest(model)
    if destination.exists() or receipt_path.exists():
        try:
            receipt = json.loads(receipt_path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError("Damaged normalized refold receipt") from exc
        if (receipt.get("source_sha256") != source_hash or receipt.get("chain_mapping") != mapping
                or receipt.get("output_sha256") != provenance.file_digest(destination)):
            raise ValueError("Damaged/changed normalized refold; preserved files")
        return destination
    normalized = Structure("refold")
    output_model = Model(0)
    normalized.add(output_model)
    for logical, original in mapping.items():
        chain = copy.deepcopy(pdb_io.get_chain(structure, original))
        chain.detach_parent()
        chain.id = logical
        output_model.add(chain)
    destination.parent.mkdir(parents=True, exist_ok=True)
    writer = MMCIFIO()
    writer.set_structure(normalized)
    writer.save(str(destination))
    exported = pdb_io.load_structure(destination)
    atom_count = 0
    for logical, original in mapping.items():
        before = list(pdb_io.get_chain(structure, original).get_atoms())
        after = list(pdb_io.get_chain(exported, logical).get_atoms())
        if (len(before) != len(after) or any(a.name != b.name or not np.allclose(a.coord, b.coord, rtol=0, atol=.001)
                                            for a, b in zip(before, after))):
            raise ValueError("Refold export changed atom correspondence")
        atom_count += len(after)
    receipt_path.write_text(json.dumps({"schema_version": 1, "source": str(model), "source_sha256": source_hash,
                             "chain_mapping": mapping, "mapping_basis": "unique_exact_sequence",
                             "output_sha256": provenance.file_digest(destination), "verified_atom_count": atom_count}, indent=2) + "\n")
    return destination


def _extract_candidates(phase: Path, contract: dict) -> pd.DataFrame:
    directory = phase / "raw/intermediate_designs_inverse_folded"
    metrics_path = directory / "aggregate_metrics_analyze.csv"
    metrics = pd.read_csv(metrics_path) if metrics_path.exists() else pd.DataFrame()
    names = {}
    for _, row in metrics.iterrows():
        filename = row.get("file_name")
        if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".cif"):
            raise ValueError("Unsafe/invalid BoltzGen generated filename")
        if filename in names:
            raise ValueError("Duplicate BoltzGen generated filename")
        names[filename] = row.to_dict()
    for path in sorted(directory.glob("*.cif")):
        names.setdefault(path.name, {})
    # Diffusion successes whose inverse fold/refold failed remain visible.
    for path in sorted((phase / "raw/intermediate_designs").glob("*.cif")):
        names.setdefault(path.name, {})
    rows = []
    for filename, raw in sorted(names.items()):
        model = directory / "refold_cif" / filename
        row = {f"boltzgen_{key}": value for key, value in raw.items()}
        row.update({"design_id": Path(filename).stem, "sequence": raw.get("designed_chain_sequence"), "source": "boltzgen",
                    "parent_engine": "boltzgen", "pred_dir": None, "cif_path": None, "target_chain": "T", "binder_chain": "H",
                    "generation_status": "failed", "generation_error": None, "tier2_requested": False, "structure_stage": "boltzgen_refold"})
        try:
            if not model.exists():
                raise ValueError("Missing BoltzGen target+binder refold structure")
            model = _reconcile_refold(model, phase, contract, raw.get("designed_chain_sequence"))
            structure = pdb_io.load_structure(model)
            sequence = pdb_io.chain_sequence(pdb_io.get_chain(structure, "H"))
            if not sequence or re.search("[^ACDEFGHIKLMNPQRSTVWY]", sequence):
                raise ValueError("Invalid generated nanobody sequence")
            recorded = raw.get("designed_chain_sequence")
            if isinstance(recorded, str) and sequence != recorded:
                raise ValueError("Generated sequence and refold structure mismatch")
            fixed = contract["scaffold_sequence"]
            variable = {i for start, end in contract["cdr_regions"].values() for i in range(start - 1, end)}
            if len(sequence) != len(fixed) or any(sequence[i] != fixed[i] for i in range(len(fixed)) if i not in variable):
                raise ValueError("Generated nanobody altered fixed scaffold residues")
            for chain, expected in contract["target_sequences"].items():
                if pdb_io.chain_sequence(pdb_io.get_chain(structure, chain)) != expected:
                    raise ValueError(f"Generated target chain {chain} sequence mismatch")
            if any(not np.isfinite(atom.coord).all() for atom in structure.get_atoms()):
                raise ValueError("Nonfinite generated coordinates")
            row.update(sequence=sequence, pred_dir=str(model), cif_path=str(model), generation_status="complete", tier2_requested=True)
        except (OSError, ValueError, KeyError, StopIteration) as exc:
            row["generation_error"] = str(exc)
        rows.append(row)
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=BASE_COLUMNS)


def run_design(cfg: dict, run_dir: Path, target: dict, mock=False, dry_run=False) -> dict:
    settings(cfg)
    phase = Path(run_dir) / "02_design/boltzgen"
    phase.mkdir(parents=True, exist_ok=True)
    if mock:
        from .design_rfantibody import FALLBACK_FRAMEWORK_SEQ
        from . import mock as mock_mod
        rows = []
        alphabet = "ADEFGHIKLMNPQRSTVWY"
        for i in range(0 if dry_run else cfg_get(cfg, "design.num_designs", 2)):
            sequence = list(FALLBACK_FRAMEWORK_SEQ)
            sequence[30] = alphabet[i % len(alphabet)]
            sequence[31] = alphabet[(i // len(alphabet)) % len(alphabet)]
            sequence = "".join(sequence)
            structure = mock_mod.build_complex_structure(target.get("sequence") or "AAAA", sequence, [], seed=i)
            model = next(structure.get_models())
            model["A"].id = "T"
            model["B"].id = "H"
            model_path = mock_mod.write_cif(structure, phase / "mock" / f"mock_{i:06d}.cif")
            rows.append({"design_id": f"mock_{i:06d}", "sequence": sequence, "source": "mock", "parent_engine": "boltzgen",
                 "generation_status": "mock", "generation_error": None, "tier2_requested": True,
                 "target_chain": "T", "binder_chain": "H", "is_mock": True, "pred_dir": str(model_path), "cif_path": str(model_path)})
        frame = pd.DataFrame(rows, columns=BASE_COLUMNS + ["is_mock"])
        if not dry_run:
            write_csv(phase / "all_generated_candidates.csv", frame)
            write_csv(phase / "design_candidates.csv", frame)
        return {"candidates": frame, "all_candidates": frame, "phase_dir": phase,
                "info": {"engine": "mock", "parent_engine": "boltzgen", "num_requested": cfg_get(cfg, "design.num_designs", 2), "num_candidates": len(frame), "is_mock": True}}
    if dry_run:
        plan = phase / "dry_run/inputs/nanobody.yaml"
        argv = command(cfg, phase / "dry_run", plan, dry_run=True)
        shell.run_in_env(resolve_path(cfg, cfg_get(cfg, "design.boltzgen.env", "envs/boltzgen")), argv, dry_run=True, log_path=phase / "boltzgen.dry-run.log")
        frame = pd.DataFrame(columns=BASE_COLUMNS)
        return {"candidates": frame, "all_candidates": frame, "phase_dir": phase,
                "info": {"engine": "boltzgen", "dry_run": True, "argv": argv, "num_candidates": 0}}
    from . import diagnostics
    env = resolve_path(cfg, cfg_get(cfg, "design.boltzgen.env", "envs/boltzgen"))
    weights = diagnostics._engine_weights(cfg, "boltzgen", env)
    if not weights.get("ok"):
        raise ValueError(weights.get("blocker", "BoltzGen models are not ready"))
    scaffold = resolve_path(cfg, cfg_get(cfg, "design.boltzgen.scaffold") or cfg_get(cfg, "design.framework"))
    inputs = [Path(target["structure"]), scaffold] if scaffold else [Path(target["structure"])]
    argv = command(cfg, phase, phase / "inputs/nanobody.yaml")
    extra_env = diagnostics._engine_extra_env(cfg, "boltzgen")
    annotation_identity = (provenance.runtime_identity(cfg, "anarci")
                           if not cfg_get(cfg, "design.boltzgen.cdr_regions") else None)
    fingerprint = provenance.fingerprint({"inputs": {str(p): provenance.file_digest(p) for p in inputs}, "target": target,
             "design": cfg.get("design"), "requested_run_seed": cfg_get(cfg, "run.seed"),
             "annotation": {"settings": cfg_get(cfg, "mature.anarci"), "scheme": cfg_get(cfg, "mature.cdr.scheme", "imgt"), "runtime": annotation_identity},
             "runtime": provenance.runtime_identity(cfg, "boltzgen", env=env, argv=argv, env_vars=extra_env)})
    marker = phase / "generation.json"
    if marker.exists() and cfg_get(cfg, "run.resume", True):
        try:
            saved = json.loads(marker.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError("Damaged BoltzGen generation receipt") from exc
        if saved.get("fingerprint") == fingerprint:
            if (not provenance.outputs_match(phase / "raw", saved.get("outputs"))
                    or not provenance.outputs_match(phase / "inputs", saved.get("canonical_inputs"))
                    or (saved.get("normalized_refolds") is not None
                        and not provenance.outputs_match(phase / "normalized_refolds", saved["normalized_refolds"]))
                    or provenance.file_digest(phase / "all_generated_candidates.csv") != saved.get("candidates_sha256")):
                raise ValueError("BoltzGen cached outputs are damaged; preserved existing files")
            all_candidates = pd.read_csv(phase / "all_generated_candidates.csv")
            candidates = all_candidates[all_candidates["generation_status"] == "complete"].copy()
            return {"candidates": candidates, "all_candidates": all_candidates, "phase_dir": phase, "info": {**saved["info"], "reused": True}}
    provenance.archive_existing([phase / name for name in ("raw", "inputs", "normalized_refolds", "scaffold_annotation", "generation.json", "all_generated_candidates.csv", "design_candidates.csv", "boltzgen.log", "generation_error.json")], phase)
    contract = prepare_specification(cfg, phase, target)
    error = None
    try:
        shell.run_in_env(env, argv, extra_env=extra_env, log_path=phase / "boltzgen.log")
    except BaseException as exc:
        error = exc
    try:
        all_candidates = _extract_candidates(phase, contract)
    except Exception as exc:
        (phase / "generation_error.json").write_text(json.dumps({"generation_error": str(error) if error else None, "normalization_error": str(exc)}) + "\n")
        if error:
            raise error from exc
        raise
    candidates = all_candidates[all_candidates["generation_status"] == "complete"].copy()
    info = {"engine": "boltzgen", "version": VERSION, "source_commit": SOURCE_COMMIT, "num_requested": cfg_get(cfg, "design.num_designs", 2),
            "num_generated_records": len(all_candidates), "num_candidates": len(candidates), "hotspot_mode": contract["hotspot_mode"],
            "seed_control": "not_supported_by_upstream_cli", "requested_run_seed": cfg_get(cfg, "run.seed"), "argv": argv,
            "generation_error": str(error) if error else None, "all_candidates_path": str(phase / "all_generated_candidates.csv")}
    write_csv(phase / "all_generated_candidates.csv", all_candidates)
    write_csv(phase / "design_candidates.csv", candidates)
    if error:
        (phase / "generation_error.json").write_text(json.dumps(info, indent=2) + "\n")
        raise error
    if not len(candidates):
        raise ValueError("BoltzGen produced no validated nanobody parents; inspect all_generated_candidates.csv and boltzgen.log")
    receipt = {"schema_version": 1, "fingerprint": fingerprint, "outputs": provenance.output_manifest(phase / "raw"),
               "canonical_inputs": provenance.output_manifest(phase / "inputs"),
               "normalized_refolds": (provenance.output_manifest(phase / "normalized_refolds")
                                      if (phase / "normalized_refolds").is_dir() else None),
               "candidates_sha256": provenance.file_digest(phase / "all_generated_candidates.csv"), "info": info}
    temporary = marker.with_suffix(".partial")
    temporary.write_text(json.dumps(receipt, indent=2) + "\n")
    temporary.replace(marker)
    return {"candidates": candidates, "all_candidates": all_candidates, "phase_dir": phase, "info": info}
