"""Attach explicit, hash-bound provenance to a separate saved-report copy."""
import copy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

from Bio.PDB import MMCIFParser, PDBParser
from .paths import contained, sha256
from .structure_review import reference_overlay


def attach_evidence(payload, evidence_dir, source_report_sha256):
    """Read only. The caller verifies the source HTML before passing its digest.

    The evidence manifest binds to that exact report, not a reused run name.
    Candidate/target identities and the explicit residue correspondence must
    agree. No scores, decision flags, coordinates or selection rules are edited.
    """
    root = Path(evidence_dir).resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("schema_version") != 1 or manifest.get("source_report_sha256") != source_report_sha256:
        raise ValueError("Supplementary evidence belongs to a different source report")

    def verified(entry):
        path = contained(root, entry["path"], exists=True)
        if sha256(path) != entry["sha256"]:
            raise ValueError("Supplementary evidence hash mismatch")
        return path

    result = {**payload, "summary": copy.deepcopy(payload["summary"]),
              "provenance": copy.deepcopy(payload.get("provenance", {}))}
    if "msa" in manifest:
        evidence = json.loads(verified(manifest["msa"]).read_text())
        rows = payload["candidates"]["rows"]
        expected = {r["candidate_id"]: hashlib.sha256(r["sequence"].encode()).hexdigest() for r in rows}
        observed = {r["candidate_id"]: r["sequence_sha256"] for r in evidence["candidates"]}
        target = payload["summary"]["state"]["phases"]["target"]["sequence"]
        if (len(observed) != len(evidence["candidates"]) or expected != observed or
                evidence["target_sequence_sha256"] != hashlib.sha256(target.encode()).hexdigest()):
            raise ValueError("MSA provenance sequence identities differ from the saved results")
        result["summary"]["msa_provenance"] = evidence
    if "reference" in manifest:
        info = manifest["reference"]
        reference = verified(info)
        mapping = json.loads(verified(manifest["mapping"]).read_text())
        if not mapping or len({(r["output_chain"], r["output_position"]) for r in mapping}) != len(mapping):
            raise ValueError("Reference mapping must contain unique explicit output positions")
        # Adapt the same contained, hash-verified reference reader used by live
        # results. The supplementary directory is the only readable root.
        reference_manifest = {"path": str(reference.relative_to(root)), "sha256": info["sha256"]}
        adapter = SimpleNamespace(root=root,
            _path=lambda name: contained(root, name),
            _json=lambda name: {"preparation": "sequence_prediction", "reference_chains": [{"id": info["target_chain"]}]})
        # Pass verified data explicitly; never overwrite an existing manifest.
        result["structures"] = []
        for structure in payload["structures"]:
            parser = PDBParser(QUIET=True) if structure["format"] == "pdb" else MMCIFParser(QUIET=True)
            model = next(parser.get_structure("saved", io.StringIO(structure["content"])).get_models())
            overlay = reference_overlay(adapter, model[structure.get("target_chain", "A")], mapping,
                                        verified=(reference, reference_manifest))
            if not overlay["available"]:
                raise ValueError("Reference could not be linked: " + overlay["reason"])
            if overlay["matched_ca"] != len(mapping):
                raise ValueError("Reference mapping includes unmatched residues")
            overlay.update({"source_id": info.get("source_id"), "anchor_chain": structure.get("target_chain", "A"),
                            "reference_anchor_chain": info["target_chain"], "role": "comparison_only",
                            "used_as_prediction_template": False})
            result["structures"].append({**structure, "reference": overlay})
        result["summary"]["reference_provenance"] = {**info, "mapping_sha256": manifest["mapping"]["sha256"],
                                                      "role": "comparison_only", "used_as_prediction_template": False}
    result["provenance"]["supplementary_evidence"] = manifest
    result["provenance"]["summary"] = result["summary"]
    return result
