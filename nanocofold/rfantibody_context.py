"""Exact, protein-only multichain bookkeeping around native RFantibody H/T PDBs."""
from __future__ import annotations

from collections import Counter
import hashlib
from io import StringIO
import json
from pathlib import Path

from Bio.Data.PDBData import protein_letters_3to1
from Bio.PDB import MMCIFParser, PDBIO, PDBParser
from Bio.PDB.Chain import Chain
from Bio.PDB.Model import Model
from Bio.PDB.PDBExceptions import PDBConstructionException
from Bio.PDB.Polypeptide import is_aa


def _load_model(path: Path, *, allow_empty: bool = False):
    suffix = path.suffix.lower()
    if suffix in {".pdb", ".ent"}:
        parser = PDBParser(QUIET=True, PERMISSIVE=False)
    elif suffix in {".cif", ".mmcif"}:
        parser = MMCIFParser(QUIET=True)
    else:
        raise ValueError(f"Unsupported structure format: {path}")
    try:
        models = list(parser.get_structure(path.stem, str(path)))
    except PDBConstructionException as exc:
        raise ValueError(f"Invalid structure (duplicate/mixed residues or atoms): {exc}") from exc
    # Legacy shell tests use an empty MODEL/ENDMDL file, which BioPython reads as zero models.
    if not models and allow_empty:
        return None
    if len(models) != 1:
        raise ValueError("RF context requires exactly one model.")
    return models[0]


def _sequence(chain: Chain) -> str:
    for residue in chain:
        if (residue.id[0] != " " or residue.resname not in protein_letters_3to1
                or residue.is_disordered() == 2 or not {"N", "CA", "C", "O"}.issubset(residue.child_dict)):
            raise ValueError(f"Chain {chain.id}: missing backbone N/CA/C/O, nonstandard or mixed residues.")
    return "".join(protein_letters_3to1[residue.resname] for residue in chain)


def _clone_chain(chain_id: str, residues) -> Chain:
    chain = Chain(chain_id)
    for position, residue in enumerate(residues, 1):
        clone = residue.copy()
        clone.detach_parent()
        clone.id = (" ", position, " ")
        chain.add(clone)
    return chain


def _pdb_text(model: Model) -> str:
    output = StringIO()
    writer = PDBIO()
    writer.set_structure(model)
    writer.save(output)
    text = output.getvalue()
    restored = PDBParser(QUIET=True, PERMISSIVE=False).get_structure("roundtrip", StringIO(text))[0]
    original_atoms = [a for c in model for r in c for a in r.get_unpacked_list()]
    restored_atoms = [a for c in restored for r in c for a in r.get_unpacked_list()]
    if len(original_atoms) != len(restored_atoms) or any(
        (before.coord != after.coord).any() or before.bfactor != after.bfactor
        for before, after in zip(original_atoms, restored_atoms)
    ):
        raise ValueError("PDB export would change coordinates or B factors; input precision is not representable.")
    return text


def _protect(path: Path, *sources: Path) -> None:
    for source in sources:
        if path.resolve() == source.resolve() or (
                path.exists() and source.exists() and path.samefile(source)):
            raise ValueError(f"Output must be separate from protected input: {source}")


def _check_existing_context(output_dir: Path, context: dict) -> None:
    """Native RF tools retain old outputs: never replace their input identity."""
    mapping = output_dir / "rf_target_mapping.json"
    error = "Existing RF context differs or is invalid; use a new design directory."
    if mapping.exists() or mapping.is_symlink():
        try:
            previous = json.loads(mapping.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(error) from exc
        if not isinstance(previous, dict):
            raise ValueError(error)
        # Compare every field, including future fields, except relocatable paths.
        paths = {"structure", "mapping_path", "source_structure"}
        old = json.dumps({k: v for k, v in previous.items() if k not in paths}, sort_keys=True)
        new = json.dumps({k: v for k, v in context.items() if k not in paths}, sort_keys=True)
        if old != new:
            raise ValueError(error)
    else:
        preview = output_dir / "rf_target.dry-run.pdb"
        if any(p != preview and p.suffix.lower() in {".qv", ".sc", ".pdb"}
               and (p.is_file() or p.is_symlink()) for p in output_dir.rglob("*")):
            raise ValueError("Existing RF artifacts without a context mapping; use a new design directory.")


def prepare_context(target_info: dict, output_dir: Path, *, dry_run=False) -> dict | None:
    """Normalize >1 antigen chains; offsets are zero-based concatenation starts.

    Single-protein inputs without declared partners (including empty fixtures) return None.
    Chain residue_labels and residue_ids describe the source; normalized positions
    are their 1-based list positions. No sequence alignment or residue inference is used.
    """
    source = Path(target_info["structure"])
    model = _load_model(source, allow_empty=True)
    chains = [] if model is None else [
        chain for chain in model if any(
            is_aa(residue) or (residue.id[0] == " " and "CA" in residue and residue["CA"].element == "C")
            for residue in chain
        )
    ]
    if len(chains) <= 1:
        if target_info.get("aux_chains"):
            raise ValueError("Partner chains declared in aux_chains are missing from the prepared structure.")
        mapping = Path(output_dir) / "rf_target_mapping.json"
        if not dry_run and (mapping.exists() or mapping.is_symlink()):
            raise ValueError("Existing RF context cannot be reused for a single-chain input; use a new design directory.")
        return None
    if any(len(chain) and chain not in chains for chain in model):
        raise ValueError("Multichain input contains nonprotein chains; context cannot be silently discarded.")
    if len(chains) > 25:
        raise ValueError("RF context supports at most 25 antigen chains.")
    primary_id = target_info.get("chain_id")
    primary = next((chain for chain in chains if chain.id == primary_id), None)
    if primary is None:
        raise ValueError("target_info.chain_id must identify the primary protein chain.")
    ordered = [primary] + [chain for chain in chains if chain is not primary]
    sequences = [_sequence(chain) for chain in ordered]
    if sequences[0] != target_info.get("sequence"):
        raise ValueError("Primary chain must exactly match the full target_info.sequence.")
    aux = target_info.get("aux_chains")
    if not isinstance(aux, list) or any(
        not isinstance(entry, dict) or not isinstance(entry.get("sequence"), str)
        or not entry["sequence"] for entry in aux
    ):
        raise ValueError("target_info.aux_chains must contain partner dictionaries with sequences.")
    if Counter(entry["sequence"] for entry in aux) != Counter(sequences[1:]):
        raise ValueError("Partner sequence multiset must exactly match aux_chains, including duplicates.")

    normalized, records, offset = Model(0), [], 0
    for index, (chain, sequence) in enumerate(zip(ordered, sequences)):
        canonical = chr(65 + index)
        normalized.add(_clone_chain(canonical, chain))
        records.append({
            "chain_id": canonical, "original_id": chain.id, "sequence": sequence,
            "length": len(sequence), "offset": offset,
            "residue_labels": [f"{chain.id}:{r.id[1]}{r.id[2].strip()}" for r in chain],
            "residue_ids": [{"number": r.id[1], "insertion_code": r.id[2].strip()} for r in chain],
        })
        offset += len(sequence)
    source_hotspots = target_info.get("hotspot_pdb_labels") or target_info.get("hotspot_labels")
    if target_info.get("hotspot_mode") == "none":
        if source_hotspots:
            raise ValueError("Unrestricted hotspot mode must have empty hotspot labels.")
        source_hotspots = []
    if not isinstance(source_hotspots, list) or (not source_hotspots and target_info.get("hotspot_mode") != "none"):
        raise ValueError("Source hotspot_pdb_labels or hotspot_labels are required.")
    labels = {label: f"A:{i}" for i, label in enumerate(records[0]["residue_labels"], 1)}
    if any(not isinstance(label, str) or label not in labels for label in source_hotspots):
        raise ValueError("Hotspot labels must exactly identify existing residues on the primary chain.")

    output_dir = Path(output_dir)
    suffix = ".dry-run" if dry_run else ""
    structure = output_dir / f"rf_target{suffix}.pdb"
    mapping_path = output_dir / f"rf_target_mapping{suffix}.json"
    protected = [source]
    if dry_run:
        protected += [output_dir / "rf_target.pdb", output_dir / "rf_target_mapping.json"]
    _protect(structure, *protected, mapping_path)
    _protect(mapping_path, *protected)
    pdb_text = _pdb_text(normalized)
    context = {
        "schema_version": 1, "structure": str(structure), "mapping_path": str(mapping_path),
        "source_structure": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "normalized_sha256": hashlib.sha256(pdb_text.encode("utf-8")).hexdigest(),
        "primary_original_id": primary_id, "chains": records, "concat_sequence": "".join(sequences),
        "hotspots": [labels[label] for label in source_hotspots],
        "hotspot_mapping": [{"source": label, "canonical": labels[label]} for label in source_hotspots],
        "dry_run": bool(dry_run),
    }
    if not dry_run:
        _check_existing_context(output_dir, context)
    output_dir.mkdir(parents=True, exist_ok=True)
    structure.write_text(pdb_text, encoding="utf-8")
    mapping_path.write_text(json.dumps(context, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return context


def restore_context(prediction_path: Path, output_path: Path, context: dict) -> dict:
    """Split an exact native T sequence and move VHH H to the next free letter.

    All output residues, including the binder, use chain-local 1-based numbering.
    The native prediction is protected against direct, symlink and hardlink overwrite.
    """
    prediction_path, output_path = Path(prediction_path), Path(output_path)
    _protect(output_path, prediction_path)
    records = context.get("chains")
    if not isinstance(records, list) or not 2 <= len(records) <= 25:
        raise ValueError("Context must describe 2 to 25 antigen chains.")
    offset, sequences = 0, []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError("Invalid context chain record.")
        sequence = record.get("sequence")
        if (not isinstance(sequence, str) or not sequence
                or record.get("chain_id") != chr(65 + index)
                or record.get("length") != len(sequence) or record.get("offset") != offset):
            raise ValueError("Context chain order, lengths or offsets are inconsistent.")
        sequences.append(sequence)
        offset += len(sequence)
    concat_sequence = "".join(sequences)
    if concat_sequence != context.get("concat_sequence"):
        raise ValueError("Context concat_sequence does not match its chain sequences.")
    model = _load_model(prediction_path)
    if {chain.id for chain in model} != {"H", "T"}:
        raise ValueError("Native VHH prediction must contain exactly protein chains H and T.")
    if _sequence(model["T"]) != concat_sequence:
        raise ValueError("Native T sequence must exactly match context concat_sequence.")
    if not _sequence(model["H"]):
        raise ValueError("Native H binder sequence is empty.")
    target_residues = list(model["T"])
    restored = Model(0)
    for record in records:
        start, length = record["offset"], record["length"]
        restored.add(_clone_chain(record["chain_id"], target_residues[start:start + length]))
    binder_chain = chr(65 + len(records))
    restored.add(_clone_chain(binder_chain, model["H"]))
    pdb_text = _pdb_text(restored)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(pdb_text, encoding="utf-8")
    return {
        "target_chain": "A", "binder_chain": binder_chain,
        "pred_dir": str(output_path), "context_restored": True,
    }
