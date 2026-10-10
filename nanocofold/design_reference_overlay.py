"""Post-inference target-only alignment of verified PDB reference complexes.

References are report inputs, never predictor inputs. Each reference is stored
once; per-model rigid transforms are applied in memory by the offline viewer.
Scientific scores and raw predicted coordinates are not changed.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import numpy as np
from Bio.PDB import MMCIFParser, PDBParser, NeighborSearch
from Bio.PDB.PDBExceptions import PDBConstructionException
from Bio.SeqUtils import seq1

from .platform.paths import contained, sha256


def _structure(asset):
    parser = MMCIFParser(QUIET=True) if asset["format"] == "cif" else PDBParser(QUIET=True)
    structure = parser.get_structure("overlay", io.StringIO(asset["content"]))
    if len(structure) != 1:
        raise ValueError("Overlay requires exactly one coordinate model")
    if any(not np.isfinite(atom.coord).all() for atom in structure.get_atoms()):
        raise ValueError("Non-finite overlay coordinates")
    return structure[0]


def _target_ca(model, chain, sequence, *, canonical_positions):
    if chain not in model:
        raise ValueError("Target chain missing from overlay coordinates")
    residues = [r for r in model[chain] if r.id[0] == " "]
    if not residues:
        raise ValueError("Overlay target has no protein residues")
    if not canonical_positions and "".join(seq1(r.resname) for r in residues) != sequence:
        raise ValueError("Predicted target sequence differs from exact campaign input")
    positions, ca = set(), {}
    for index, residue in enumerate(residues, 1):
        position = residue.id[1] if canonical_positions else index
        if (canonical_positions and residue.id[2].strip()) or position in positions:
            raise ValueError("Ambiguous canonical reference residue position")
        positions.add(position)
        if position < 1 or position > len(sequence) or seq1(residue.resname) != sequence[position - 1]:
            raise ValueError("Reference residue differs from canonical target sequence")
        if "CA" in residue:
            ca[position] = residue["CA"].coord.astype(np.float64)
    return ca


def _fit(moving, fixed):
    """Proper row-vector rigid transform: reference @ rotation + translation."""
    moving, fixed = np.asarray(moving, dtype=np.float64), np.asarray(fixed, dtype=np.float64)
    if len(moving) < 3:
        raise ValueError("At least three matched target Cα atoms are required")
    a, b = moving.mean(axis=0), fixed.mean(axis=0)
    if min(np.linalg.matrix_rank(moving - a), np.linalg.matrix_rank(fixed - b)) < 2:
        raise ValueError("Target Cα alignment is collinear or degenerate")
    u, _, vt = np.linalg.svd((moving - a).T @ (fixed - b))
    correction = np.diag([1., 1., 1. if np.linalg.det(u @ vt) > 0 else -1.])
    rotation = u @ correction @ vt
    translation = b - a @ rotation
    rmsd = float(np.sqrt(np.mean(np.sum((moving @ rotation + translation - fixed) ** 2, axis=1))))
    return rotation, translation, rmsd


def _contact_footprint(model, target_chain, binder_chain, *, canonical_positions):
    """Target residues with a protein heavy atom within 5 Å of the VHH.

    Contact sets are invariant under rigid rotation/translation. Compare target
    sequence positions only, so differently sequenced VHHs need no atom mapping.
    """
    if binder_chain == target_chain or binder_chain not in model:
        raise ValueError("VHH chain missing or ambiguous for binding-site comparison")
    residues = [r for r in model[target_chain] if r.id[0] == " "]
    heavy = lambda r: [a for a in r.get_atoms() if a.element not in {"H", "D"}]
    binder_atoms = [a for r in model[binder_chain] if r.id[0] == " " for a in heavy(r)]
    if not binder_atoms:
        raise ValueError("VHH has no protein heavy atoms")
    neighbors = NeighborSearch(binder_atoms)
    observed, contacts = set(), set()
    for index, residue in enumerate(residues, 1):
        position = residue.id[1] if canonical_positions else index
        atoms = heavy(residue)
        if atoms:
            observed.add(position)
        if any(neighbors.search(atom.coord, 5.0) for atom in atoms):
            contacts.add(position)
    return observed, contacts


def _compare_footprints(reference, predicted):
    reference_observed, reference_contacts = reference
    predicted_observed, predicted_contacts = predicted
    comparable = reference_observed & predicted_observed
    ref, pred = reference_contacts & comparable, predicted_contacts & comparable
    shared, union = ref & pred, ref | pred
    return {"available": bool(ref), "method": "target_heavy_atom_contact_footprint_5A",
            "distance_cutoff_angstrom": 5.0,
            "reason": None if ref else "No reference contact residues with comparable target coordinates",
            "comparable_target_positions": sorted(comparable),
            "reference_contact_positions": sorted(ref), "predicted_contact_positions": sorted(pred),
            "shared_contact_positions": sorted(shared), "reference_contacts": len(ref),
            "predicted_contacts": len(pred), "shared_contacts": len(shared),
            "reference_contact_recovery": len(shared) / len(ref) if ref else None,
            "contact_jaccard": len(shared) / len(union) if ref and union else None,
            "reference_contacts_outside_comparable": len(reference_contacts - comparable),
            "predicted_contacts_outside_comparable": len(predicted_contacts - comparable),
            "interpretation": "Target epitope overlap only; not native DockQ, pose accuracy, binding or KD"}


def attach_reference_overlays(payload: dict, manifest_path: Path | None) -> None:
    """Attach separately audited visual metadata; do not mutate model records."""
    payload.update(reference_coordinates={}, reference_structures=[], reference_overlays={}, reference_sources=[], reference_binding_sites={})
    if manifest_path is None:
        return
    manifest_path = Path(manifest_path).resolve()
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("references"), list):
        raise ValueError("Unsupported report reference manifest")
    targets = {t["pdb_id"]: t for t in payload["targets"]}
    references = {}
    receipts = [{"kind": "report_reference_manifest", "path": manifest_path.name,
                 "sha256": hashlib.sha256(manifest_bytes).hexdigest(), "size": len(manifest_bytes)}]
    for row in manifest["references"]:
        pdb_id = row.get("pdb_id")
        if pdb_id not in targets or pdb_id in references:
            raise ValueError("Unknown or duplicate report reference target")
        target = targets[pdb_id]
        if row.get("target_sequence_sha256") != target["sequence_sha256"]:
            raise ValueError("Report reference target sequence hash differs")
        if row.get("residue_position_mode") != "canonical_sequence_1_based":
            raise ValueError("Reference requires explicit canonical residue positions")
        path = contained(manifest_path.parent, row["path"], exists=True)
        digest = sha256(path)
        if digest != row.get("sha256"):
            raise ValueError("Report reference coordinate hash differs")
        if path.suffix.lower() != ".pdb":
            raise ValueError("Report reference must be a normalized PDB")
        asset = {"content": path.read_text(encoding="utf-8"), "format": "pdb"}
        if hashlib.sha256(asset["content"].encode()).hexdigest() != digest:
            raise ValueError("Report reference UTF-8 bytes changed")
        model = _structure(asset)
        ca = _target_ca(model, row["target_chain"], target["sequence"], canonical_positions=True)
        if row["binder_chain"] == row["target_chain"] or row["binder_chain"] not in model:
            raise ValueError("Native reference VHH chain missing or ambiguous")
        metadata = {key: row.get(key) for key in ("pdb_id", "target_chain", "binder_chain", "target_sequence_sha256",
                    "residue_position_mode", "source_url", "assembly_sha256", "source_manifest_sha256")}
        metadata.update(structure_key=digest, structure_sha256=digest, matched_target_ca_available=len(ca))
        payload["reference_structures"].append(metadata)
        payload["reference_coordinates"].setdefault(digest, asset)
        footprint = _contact_footprint(model, row["target_chain"], row["binder_chain"], canonical_positions=True)
        references[pdb_id] = (metadata, ca, footprint)
        receipts.append({"kind": "report_reference_coordinates", "pdb_id": pdb_id, "path": row["path"],
                         "sha256": digest, "size": path.stat().st_size})
    # Fail closed if an explicitly supplied reference changes while being read.
    for receipt in receipts:
        if sha256(contained(manifest_path.parent, receipt["path"], exists=True)) != receipt["sha256"]:
            raise ValueError("Report reference changed during extraction")
    payload["reference_sources"] = receipts
    for row in payload["models"]:
        result = {"available": False, "method": "target_CA_rigid_fit", "reason": "No supplied PDB reference"}
        site = {"available": False, "reason": "No supplied PDB reference"}
        reference = references.get(row["pdb_id"])
        if reference is not None:
            metadata, moving_ca, footprint = reference
            result.update(reference_key=metadata["structure_key"], reference_sha256=metadata["structure_sha256"])
            asset = payload["coordinates"].get(row["structure_key"])
            if asset is None:
                result["reason"] = "No stored predicted coordinates"
                site["reason"] = result["reason"]
            else:
                try:
                    predicted = _structure(asset)
                    fixed_ca = _target_ca(predicted, row["target_chain"], targets[row["pdb_id"]]["sequence"], canonical_positions=False)
                    positions = sorted(moving_ca.keys() & fixed_ca.keys())
                    rotation, translation, rmsd = _fit([moving_ca[p] for p in positions], [fixed_ca[p] for p in positions])
                    result.update(available=True, reason=None, rotation=rotation.tolist(), translation=translation.tolist(),
                                  matched_ca=len(positions), target_sequence_positions=positions, rmsd_angstrom=rmsd)
                    site = _compare_footprints(footprint, _contact_footprint(predicted, row["target_chain"], row.get("binder_chain", "B"), canonical_positions=False))
                    site.update(reference_sha256=metadata["structure_sha256"], predicted_sha256=row["structure_key"])
                except (ValueError, KeyError, PDBConstructionException) as exc:
                    if not result["available"]:
                        result["reason"] = str(exc)
                    site["reason"] = str(exc)
        payload["reference_overlays"][row["id"]] = result
        payload["reference_binding_sites"][row["id"]] = site
