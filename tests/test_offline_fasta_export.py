"""Offline FASTA downloads preserve the authoritative complete input sequences."""
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path

import pytest
import yaml

from nanocofold.platform.results import ResultReader
from test_audit21_presentation import saved_run
from test_complex_legacy_pae import saved_complex


def _payload(document):
    return json.loads(re.search(r'<script id="result-data" type="application/json">(.*?)</script>', document.decode(), re.S)[1])


@pytest.mark.parametrize("mode", ["screen", "complex"])
def test_html_and_shareable_zip_embed_same_complete_fasta_as_api(tmp_path, mode):
    if mode == "complex":
        root, _ = saved_complex(tmp_path, None)
        cfg = yaml.safe_load((root / "run_config.yaml").read_text())
        cfg["complex"]["proteins"] = [{"name": "단백질 copies", "sequence": "ACDEFG", "start": 2, "end": 5,
                                        "copies": 2, "original_positions": [-3, -2, -1, 1, 5, 8], "reference_chain": "X"}]
        (root / "run_config.yaml").write_text(yaml.safe_dump(cfg))
    else:
        root, _ = saved_run(tmp_path, count=2)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    reader = ResultReader(root)
    expected = reader.export("fasta")[0]
    filters = {"search": "nonexistent candidate"}
    html = _payload(reader.export("html", filters)[0])
    with zipfile.ZipFile(io.BytesIO(reader.export("report_zip", filters)[0])) as archive:
        delivery = _payload(archive.read("report.html"))
        assert archive.read("sequences.fasta") == expected
    assert html["sequences_fasta"].encode() == delivery["sequences_fasta"].encode() == expected
    assert html["provenance"]["filtered_ids"] == delivery["provenance"]["filtered_ids"] == []
    assert expected.count(b">") == 2
    if mode == "complex":
        assert expected.count(b"CDEF\n") == 2 and expected.count(b"original_positions=-2,-1,1,5") == 2
        assert not any(row.get("sequence") for row in html["candidates"]["rows"])
    assert all(path.read_bytes() == content for path, content in before.items())


def test_missing_historical_complex_input_does_not_block_saved_score_html(tmp_path):
    root, _ = saved_complex(tmp_path, None)
    (root / "run_config.yaml").write_text("run:\n  mode: complex\n")
    result = _payload(ResultReader(root).export("html")[0])
    assert result["sequences_fasta"] is None
    assert result["candidates"]["rows"][0]["candidate_id"] == "a"


def test_actual_shared_and_embedded_ui_fasta_download_contract():
    contract = Path(__file__).with_name("offline_fasta_export_contract.mjs")
    result = subprocess.run(["node", "--test", str(contract)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
