"""Adversarial presentation/export regressions, using isolated CPU fixtures."""
import base64
import copy
import gzip
import hashlib
import io
import json
import zipfile

import numpy as np
import pandas as pd
import pytest

from nanocofold import report
from nanocofold.platform.results import ResultReader, _pack_offline_pae
from nanocofold.platform.cloud_results import _presentation_data_identity, build_bundle, import_nrf2
from nanocofold.report_delivery import package_report
from nanocofold.utils.presentation import portable_metadata
from nanocofold.utils.spreadsheet import excel_text_chunks
from test_audit21_presentation import saved_run


def test_cli_report_redacts_context_engine_environment_and_paths_preserving_original(tmp_path):
    run, cfg = saved_run(tmp_path, count=2)
    cfg["report"]["viewer"] = "none"
    directory = run / "02_comparison"
    directory.mkdir(exist_ok=True)
    sentinel = "/cluster/PRIVATE_EXAMPLE_SENTINEL"
    (directory / "comparison_identity.json").write_text(json.dumps({"runtime": {"boltz2": {
        "environment": {"PYTHONPATH": sentinel + "/engine:/other-private-mount/modules",
                        "CONDA_PREFIX": sentinel + "/env", "CUSTOM_CACHE": sentinel + "/cache",
                        "HF_HOME": "/lustre/private/cache"},
        "source": {sentinel + "/predict.py": "a" * 64}}}}))
    originals = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    doc = report.generate_report(run, cfg, {"receipt": {"note": "failed /beegfs/private/model.cif"}}).read_text()
    assert str(run) not in doc
    assert sentinel not in doc and "/lustre/private" not in doc and "/beegfs/private" not in doc
    assert "[local path]" in doc
    assert all(p.read_bytes() == data for p, data in originals.items())


def test_custom_mounts_redacted_without_corrupting_scientific_content():
    opaque = {"content": "ATOM /zfs/native", "raw_content": "/lustre/native", "smiles": "/zfs/CC",
              "array": {"encoding": "gzip-base64-float64le", "data": "/zfs/" + "a" * 70},
              "note": "failed /beegfs/private/file.cif; open https://example.org/zfs/public"}
    value = portable_metadata(opaque)
    assert all(value[key] == opaque[key] for key in ("content", "raw_content", "smiles", "array"))
    assert "/beegfs/private" not in value["note"]
    assert "https://example.org/zfs/public" in value["note"]


def test_relative_first_runtime_path_lists_with_custom_mounts_and_windows_drives():
    env = {"PYTHONPATH": ".:/cluster/private/modules", "PATH": "bin:/customfs/private/bin",
           "LD_LIBRARY_PATH": "lib:/cluster/private/lib", "CPATH": "headers:/cluster/private/include",
           "WINDOWS": "C:\\private\\env", "VIRTUAL_ENV": "/cluster/private/env"}
    portable = portable_metadata({"environment": env})["environment"]
    assert portable["PYTHONPATH"] == ".:[local path]"
    assert portable["PATH"] == "bin:[local path]" and portable["CPATH"] == "headers:[local path]"
    assert portable["LD_LIBRARY_PATH"] == "lib:[local path]"
    assert portable["WINDOWS"] == portable["VIRTUAL_ENV"] == "[local path]"
    assert portable_metadata({"environment": {"PATH": "bin;C:\\private\\bin;D:\\tools"}})["environment"]["PATH"] == "bin;[local path];[local path]"
    assert portable_metadata({"path": r"\\\\?\C:\private\models\weights.pt"})["path"] == "[local path]"


def test_pae_is_losslessly_pooled_with_structure_specific_axes():
    original = np.array([[0., .12345678901234567], [29.999999999999996, 2.]], dtype="<f8")
    arrays = {}
    structures = []
    for labels in (["A:1", "B:1"], ["C:2", "D:3"]):
        structure = {"pae": {"values": original.tolist(), "labels": labels, "unit": "Å"}}
        structures.append(_pack_offline_pae(structure, arrays))
    assert len(arrays) == 1
    key, array = next(iter(arrays.items()))
    raw = gzip.decompress(base64.b64decode(array["data"]))
    assert raw == original.tobytes() and hashlib.sha256(raw).hexdigest() == key
    assert array["bytes"] == len(raw) and array["dimension"] == 2
    assert structures[0]["pae"]["labels"] != structures[1]["pae"]["labels"]
    assert all(s["pae"]["values_ref"] == key and "values" not in s["pae"] for s in structures)
    assert _pack_offline_pae({"pae": None}, arrays) == {"pae": None}


@pytest.mark.parametrize("values,labels", [([[1, 2]], ["A:1"]), ([[float("nan")]], ["A:1"]), ([[0]], [])])
def test_invalid_pae_never_becomes_an_encoded_success(values, labels):
    with pytest.raises(ValueError):
        _pack_offline_pae({"pae": {"values": values, "labels": labels}}, {})


def test_offline_payload_pools_all_observations_without_dropping_records(tmp_path, monkeypatch):
    run, _ = saved_run(tmp_path, count=2)
    reader = ResultReader(run)
    monkeypatch.setattr(reader, "structure", lambda candidate, observation=None: {
        "candidate_id": candidate, "observation_id": observation, "content": "native bytes", "format": "cif",
        "pae": {"values": [[0., 1.25], [2.5, 0.]], "labels": ["A:1", "B:1"], "unit": "Å"}})
    payload = reader._offline_payload(reader.candidates(limit=None), {}, {})
    assert len(payload["structures"]) == 4
    assert len(payload["pae_arrays"]) == 1
    assert len(payload["candidates"]["rows"]) == 2 and len(payload["observations"]["rows"]) == 2
    assert all("values" not in s["pae"] for s in payload["structures"])


def test_export_summary_does_not_count_failed_rows_as_scored_or_eligible(tmp_path):
    run, _ = saved_run(tmp_path, count=2)
    path = run / "03_score/scored_candidates.csv"
    frame = pd.read_csv(path)
    frame.loc[0, ["error", "final_evaluated", "final_eligible"]] = ["recorded scoring failure", True, True]
    frame.to_csv(path, index=False)
    summary = ResultReader(run).summary()
    assert summary["model_record_total"] == 2
    assert summary["scored_record_total"] == summary["scored_total"] == 1
    assert summary["eligible_total"] <= 1
    assert summary["status"] == "partial_failure"


def test_presentation_zip_is_byte_reproducible_with_fixed_member_metadata():
    files = {"report.html": b"example", "candidates.csv": b"id\nx\n"}
    first = package_report(files, {"summary": {}})
    second = package_report(dict(reversed(list(files.items()))), {"summary": {}})
    assert first == second
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
        manifest = json.loads(archive.read("delivery_manifest.json"))
        assert all(hashlib.sha256(archive.read(row["path"])).hexdigest() == row["sha256"] for row in manifest["files"])


def test_utf16_provenance_chunks_reconstruct_supplementary_unicode():
    text = json.dumps({"receipt": "🧬가" * 16000}, ensure_ascii=False)
    chunks = excel_text_chunks(text)
    assert len(chunks) > 1 and "".join(chunks) == text
    assert all(len(part.encode("utf-16-le")) // 2 <= 30000 for part in chunks)


def test_report_file_index_declares_omitted_entries(tmp_path):
    for number in range(203):
        (tmp_path / f"record-{number:03}.json").write_text("{}")
    document = report.generate_report(tmp_path, {"run": {"mode": "screen"}, "target": {"hotspots": []},
                                               "report": {"viewer": "none"}}, {}, scored=pd.DataFrame()).read_text()
    assert 'data-file-list-omitted="3"' in document
    assert "record-199.json" in document and "record-200.json" not in document


def test_presentation_identity_requires_all_scientific_records_and_axes():
    payload = {"candidates": {"rows": [{"candidate_id": "x"}]}, "observations": {"rows": []},
               "structures": [{"content": "original", "pae": {"labels": ["A:1"]}}],
               "summary": {"config": {"score": {"thresholds": {"ipsae_min": .7}}}}, "provenance": {}}
    def html(value):
        return b'<script id="result-data" type="application/json">' + json.dumps(value).encode() + b'</script>'
    baseline = _presentation_data_identity(html(payload))
    changed = copy.deepcopy(payload)
    changed["structures"][0]["pae"]["labels"] = ["B:2"]
    assert baseline and baseline != _presentation_data_identity(html(changed))
    changed = copy.deepcopy(payload)
    changed["summary"]["config"]["score"]["thresholds"]["ipsae_min"] = .95
    assert baseline != _presentation_data_identity(html(changed))
    assert _presentation_data_identity(b"unknown older report") is None


def test_nrf_import_summary_matches_two_observations_without_changing_source(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    (source / "analysis.json").write_text(json.dumps({"campaign": {}, "observations": [
        {"seed": seed, "metrics": {}, "structure": {"content": "native bytes", "format": "pdb"}}
        for seed in [11, 22]]}))
    for name in ("comparison.csv", "KEAP1_NRF2_ETGE_report.html", "KEAP1_NRF2_ETGE_package.zip"):
        (source / name).write_bytes(b"unchanged")
    before = {p: p.read_bytes() for p in source.iterdir()}
    result = import_nrf2(source, destination)
    assert result["total"] == json.loads((destination / "index.json").read_text())["summary"]["total"] == 2
    assert all(p.read_bytes() == data for p, data in before.items())


def test_bundle_omits_only_equivalent_generated_views_preserving_native_and_old_reports(tmp_path):
    run, cfg = saved_run(tmp_path, count=2)
    report.generate_report(run, cfg, {})
    explorer = report.report_explorer_path(run / "report.html")
    historical = run / ("report_old_explorer-" + "a" * 64 + ".html")
    historical.write_text("unknown historical snapshot")
    old_delivery = run / "report_delivery.zip"
    old_delivery.write_bytes(package_report({"report.html": b"historical distinct report"}, {}))
    before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    destination = tmp_path / "bundle"
    build_bundle(run, destination)
    with zipfile.ZipFile(destination / "results.zip") as archive:
        assert "original/03_score/scored_candidates.csv" in archive.namelist()
        assert "original/" + historical.name in archive.namelist()
        assert archive.read("original/report_delivery.zip") == before[old_delivery]
        assert "original/" + explorer.name not in archive.namelist()
        assert "original/report.html" not in archive.namelist()
        receipt = json.loads(archive.read("report_packaging_manifest.json"))
        omitted = {entry["path"]: entry for entry in receipt["derived_reports_omitted"]}
        assert omitted["original/" + explorer.name]["scientific_payload_sha256"]
        assert omitted["original/report.html"]["separate_download"] == "report_delivery.zip"
    assert all(p.read_bytes() == data for p, data in before.items())


def test_bundle_keeps_historical_report_with_different_thresholds_without_reranking(tmp_path):
    import yaml
    run, cfg = saved_run(tmp_path, count=2)
    report.generate_report(run, cfg, {})
    explorer = report.report_explorer_path(run / "report.html")
    old_explorer, old_entry = explorer.read_bytes(), (run / "report.html").read_bytes()
    (run / "report_delivery.zip").write_bytes(ResultReader(run).export("report_zip")[0])
    old_package = (run / "report_delivery.zip").read_bytes()
    config_path = run / "run_config.yaml"
    changed = yaml.safe_load(config_path.read_text())
    changed.setdefault("score", {}).setdefault("thresholds", {})["ipsae_min"] = .95
    config_path.write_text(yaml.safe_dump(changed, allow_unicode=True))
    rows_before = (run / "03_score/scored_candidates.csv").read_bytes()
    destination = tmp_path / "bundle"
    build_bundle(run, destination)
    with zipfile.ZipFile(destination / "results.zip") as archive:
        assert archive.read("original/" + explorer.name) == old_explorer
        assert archive.read("original/report.html") == old_entry
        assert archive.read("original/report_delivery.zip") == old_package
    assert (run / "03_score/scored_candidates.csv").read_bytes() == rows_before
