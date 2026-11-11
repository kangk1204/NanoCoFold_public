"""Regression checks for independent, CPU-only audits of saved model outputs."""
import copy
import json

import pytest

from nanocofold.platform.offline_assets import compact_payload
from scripts.boltz_msa_link import parsed_msa_for_chain
from scripts.expand_report_payload import expand_report_payload


def test_native_msa_link_follows_chain_name_not_file_number(tmp_path):
    root = tmp_path / "processed"
    (root / "records").mkdir(parents=True)
    (root / "msa").mkdir()
    chains = [{"chain_name": name, "num_residues": 3, "mol_type": 0, "valid": True, "msa_id": msa}
              for name, msa in [("A", "complex_1"), ("B", "complex_0")]]
    record = root / "records/complex.json"
    record.write_text(json.dumps({"chains": chains}))
    for item in chains:
        (root / "msa" / (item["msa_id"] + ".npz")).touch()
    for item in chains:
        path, entry, source = parsed_msa_for_chain(root, item["chain_name"], 3)
        assert (path.name, entry, source) == (item["msa_id"] + ".npz", item, record)
    with pytest.raises(ValueError, match="identity"):
        parsed_msa_for_chain(root, "A", 2)
    record.write_text(json.dumps({"chains": chains + [chains[0]]}))
    with pytest.raises(ValueError, match="one explicit"):
        parsed_msa_for_chain(root, "A", 3)
    record.write_text(json.dumps({"chains": chains}))
    path = root / "msa/complex_1.npz"
    path.unlink()
    outside = tmp_path / "outside.npz"
    outside.touch()
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="uncontained"):
        parsed_msa_for_chain(root, "A", 3)
    chains[0]["msa_id"] = "../outside"
    record.write_text(json.dumps({"chains": chains}))
    with pytest.raises(ValueError, match="identifier"):
        parsed_msa_for_chain(root, "A", 3)


def test_expand_round_trip_and_tampering_on_saved_observations():
    model = {"candidate_id": "V1", "content": "ATOM example\n", "raw_content": "ATOM example\n",
             "format": "pdb", "raw_format": "pdb", "pae": {"values_ref": "existing-array"}}
    original = {"structures": [model, {**model, "observation_id": "seed42"}],
                "candidates": {"rows": [{"candidate_id": "V1", "score": 0.75}]}, "pae_arrays": {}}
    assert expand_report_payload(original) is original
    compact = compact_payload(original, min_bytes=0)
    frozen = copy.deepcopy(compact)
    assert len(compact["structure_assets"]) == 1
    assert expand_report_payload(compact) == original
    assert compact == frozen
    key = compact["structures"][0]["structure_ref"]
    corrupt = copy.deepcopy(compact)
    corrupt["structure_assets"][key]["bytes"] -= 1
    with pytest.raises(ValueError, match="hash or size"):
        expand_report_payload(corrupt)
    corrupt = copy.deepcopy(compact)
    corrupt["structure_assets"][key]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash or size"):
        expand_report_payload(corrupt)
    compact["structures"][0]["format"] = "cif"
    with pytest.raises(ValueError, match="Ambiguous"):
        expand_report_payload(compact)
    with pytest.raises(ValueError, match="Unsupported"):
        expand_report_payload({"offline_asset_format": "unknown"})
