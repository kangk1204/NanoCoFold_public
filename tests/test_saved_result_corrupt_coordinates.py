"""Malformed saved coordinates must not prevent delivery of intact models."""
import hashlib
import io
import json
import re
import zipfile
from html.parser import HTMLParser
from urllib.parse import unquote

import numpy as np
import pandas as pd
import pytest
import yaml

from nanocofold import mock, report
from nanocofold.platform.results import ResultReader


class ExplorerFrame(HTMLParser):
    href = None

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "a" and attributes.get("id") == "complete-result-explorer":
            self.href = attributes["href"]


def payload(document):
    match = re.search(r'<script id="result-data" type="application/json">(.*?)</script>', document, re.S)
    assert match
    return json.loads(match.group(1))


def saved_run(tmp_path, malformed):
    run = tmp_path / "saved"
    (run / "03_score").mkdir(parents=True)
    (run / "good").mkdir()
    (run / "broken").mkdir()
    target, binder = "ACDE", "ACCDE"
    good = mock.write_pdb(mock.build_complex_structure(target, binder, [], seed=42),
                          run / "good/colabfold_model_0.pdb")
    (run / "broken/model.pdb").write_text(malformed)
    matrix = np.arange(81, dtype=np.float32).reshape(9, 9) / 20
    np.savez(good.parent / "pae_colabfold_model_0.npz", pae=matrix)
    cfg = {"run": {"mode": "screen", "mock": True}, "target": {"sequence": target},
           "report": {"viewer": "3dmol", "top_n_table": 2}}
    (run / "run_config.yaml").write_text(yaml.safe_dump(cfg))
    (run / "run_state.json").write_text(json.dumps({"phases": {"final": {}}}))
    rows = [{"design_id": name, "sequence": binder, "cif_path": path,
             "predictor_engine": "colabfold", "target_chain": "A", "binder_chain": "B",
             "ipsae": .2, "iptm": .4, "final_evaluated": True, "final_eligible": False}
            for name, path in [("ok", "good/colabfold_model_0.pdb"), ("broken", "broken/model.pdb")]]
    pd.DataFrame(rows).to_csv(run / "03_score/scored_candidates.csv", index=False)
    return run, cfg, matrix


@pytest.mark.parametrize("malformed", ["test structure\n", "MODEL        1\nENDMDL\nEND\n"])
def test_corrupt_saved_model_is_unavailable_but_other_model_and_delivery_survive(tmp_path, malformed):
    run, cfg, matrix = saved_run(tmp_path, malformed)
    originals = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in run.rglob("*") if p.is_file()}
    reader = ResultReader(run)
    healthy = reader.structure("ok")
    assert np.array_equal(np.asarray(healthy["pae"]["values"]), matrix.astype(float))
    with pytest.raises(ValueError, match="구조를 표시할 수 없습니다.*원자 좌표"):
        reader.structure("broken")

    exported = [payload(reader.export("html")[0].decode())]
    with zipfile.ZipFile(io.BytesIO(reader.export("report_zip")[0])) as archive:
        assert archive.testzip() is None
        exported.append(payload(archive.read("report.html").decode()))
    output = report.generate_report(run, cfg, {})
    document = output.read_text()
    frame = ExplorerFrame(); frame.feed(document)
    assert frame.href
    exported.append(payload((output.parent / unquote(frame.href)).read_text()))
    for result in exported:
        assert {row["candidate_id"] for row in result["candidates"]["rows"]} == {"ok", "broken"}
        assert result["structures"]
        assert all(s["candidate_id"] == "ok" for s in result["structures"])
        assert any("broken" in issue and "원자 좌표" in issue for issue in result["summary"]["issues"])
        for structure in result["structures"]:
            for key in ("content", "raw_content", "chains", "pae", "contacts", "clashes"):
                assert structure[key] == healthy[key]
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == before for p, before in originals.items())
