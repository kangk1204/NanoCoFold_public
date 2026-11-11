"""Locate a protein chain's parsed MSA in a saved Boltz run without running Boltz.

Use the records/complex.json in the NanoCoFold run's Boltz processed directory.
The returned path identifies the recorded archive; check its query sequence
separately if sequence identity rather than file linkage is required.
"""
import json
from pathlib import Path


def parsed_msa_for_chain(processed, chain, residue_count):
    """Return (NPZ path, native chain entry, native record path)."""
    root = Path(processed).resolve(strict=True)
    record_path = root / "records" / "complex.json"
    if not record_path.resolve().is_relative_to(root):
        raise ValueError("Boltz record points outside the processed directory")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    matches = [entry for entry in record["chains"] if entry["chain_name"] == chain]
    if len(matches) != 1:
        raise ValueError("Boltz chain name must have one explicit MSA binding")
    entry = matches[0]
    if (type(residue_count) is not int or residue_count < 1 or
            entry["num_residues"] != residue_count or entry["mol_type"] != 0 or entry["valid"] is not True):
        raise ValueError("Boltz chain identity does not match the requested protein")
    identifier = entry["msa_id"]
    if (not isinstance(identifier, str) or identifier in {"", ".", ".."} or
            Path(identifier).name != identifier or "\\" in identifier):
        raise ValueError("Invalid Boltz MSA identifier")
    folder = (root / "msa").resolve()
    path = root / "msa" / (identifier + ".npz")
    if not folder.is_relative_to(root) or not path.resolve().is_relative_to(folder) or not path.is_file():
        raise ValueError("Missing or uncontained Boltz parsed MSA")
    return path, entry, record_path
