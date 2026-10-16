"""Missing legacy coordinate-axis metadata must not invalidate saved PAE."""
import base64
import gzip
import hashlib
import io
import json
import re
import zipfile

import numpy as np
import pytest
import yaml

from nanocofold import mock
from nanocofold.platform.results import ResultReader


def saved_complex(tmp_path, chains):
    root = tmp_path / "saved"
    root.mkdir()
    content = mock.write_pdb(mock.build_complex_structure("A", "C", [], seed=42),
                             root / "native.pdb").read_text()
    pae = {"values": [[0, 1.23456789], [3.987654321, 0]], "labels": ["A:1", "B:1"], "unit": "Å"}
    structure = {"content": content, "format": "pdb", "pae": pae}
    if chains is not None:
        structure["chains"] = chains
    (root / "model.json").write_text(json.dumps(structure))
    (root / "run_config.yaml").write_text(yaml.safe_dump({"run": {"mode": "complex"},
        "complex": {"proteins": [{"sequence": "A"}, {"sequence": "C"}]}}))
    (root / "run_state.json").write_text(json.dumps({"phases": {"final": {}}, "execution": {"state": "complete"}}))
    (root / "complex_results.json").write_text(json.dumps({"rows": [{"candidate_id": "a", "evaluated": True,
        "iptm": .8, "structure_available": True, "structure_file": "model.json"}]}))
    return root, structure


@pytest.mark.parametrize("chains", [None, [], [{"id": "A"}, {"id": "B"}],
    [{"id": "A", "residues": [{"label": "A:1"}]}, {"id": "B"}]],
    ids=["no_chain_metadata", "empty_chain_metadata", "chain_ids_only", "partial_residue_axis"])
def test_legacy_valid_pae_keeps_exact_values_without_inventing_coordinate_axis(tmp_path, chains):
    root, original = saved_complex(tmp_path, chains)
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}
    live = ResultReader(root).structure("a")
    assert live["pae"] == original["pae"] and live["content"] == original["content"]
    assert live.get("pae_status") != "invalid"
    assert live.get("chains") == original.get("chains")
    documents = [ResultReader(root).export("html")[0].decode()]
    with zipfile.ZipFile(io.BytesIO(ResultReader(root).export("report_zip")[0])) as archive:
        documents.append(archive.read("report.html").decode())
    expected = np.asarray(original["pae"]["values"], dtype="<f8").tobytes()
    for document in documents:
        payload = json.loads(re.search(r'<script id="result-data" type="application/json">(.*?)</script>', document, re.S).group(1))
        assert len(payload["structures"]) == 1
        structure = payload["structures"][0]
        assert structure["content"] == original["content"]
        assert structure["pae"]["labels"] == original["pae"]["labels"]
        packed = payload["pae_arrays"][structure["pae"]["values_ref"]]
        assert gzip.decompress(base64.b64decode(packed["data"])) == expected
        assert not any("PAE 자료가 손상" in issue for issue in payload["summary"]["issues"])
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in before.items())


@pytest.mark.parametrize("residue", [{}, {"label": 1}])
def test_fully_recorded_residue_axis_with_invalid_label_remains_rejected(tmp_path, residue):
    root, original = saved_complex(tmp_path, [{"id": "A", "residues": [residue]},
                                             {"id": "B", "residues": [{"label": "B:1"}]}])
    before = (root / "model.json").read_bytes()
    record = ResultReader(root).structure("a")
    assert record["pae"] is None and record["pae_status"] == "invalid"
    assert record["content"] == original["content"]
    assert (root / "model.json").read_bytes() == before
