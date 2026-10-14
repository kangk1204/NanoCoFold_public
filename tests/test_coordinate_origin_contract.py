"""Read-only coordinate-origin contracts; synthetic files are CPU fixtures."""
import copy
import hashlib
import json

import pytest
import yaml

from nanocofold import mock
from nanocofold.platform.results import ResultReader


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def inventory(root):
    return {str(p.relative_to(root)): digest(p.read_bytes()) for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def vhh_run(tmp_path):
    def create(mode="screen", engine="boltz2", *, receipt=True, crlf=False):
        root = tmp_path / (mode + engine)
        output = {"boltz2": "boltz_out", "colabfold": "colabfold_out", "chai1": "chai_out", "opendde": "opendde_out"}[engine]
        marker = {"boltz2": "boltz_complete.json", "colabfold": "colabfold_complete.json"}.get(engine, "prediction_complete.json")
        folder = root / "02_prediction/a"
        path = folder / output / "model.pdb"
        path.parent.mkdir(parents=True)
        mock.write_pdb(mock.build_complex_structure("ACDEFG", "LMNPQR", [], seed=7), path)
        if crlf:
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        (root / "run_config.yaml").write_text(yaml.safe_dump({"run": {"mode": mode, "mock": False}}))
        (root / "03_score").mkdir()
        (root / "03_score/scored_candidates.csv").write_text(
            "candidate_id,sequence,cif_path,target_chain,binder_chain,engine\n"
            f"a,LMNPQR,{path.relative_to(root)},A,B,{engine}\n")
        if receipt:
            (folder / marker).write_text(json.dumps({"status": "complete", "outputs": {"model.pdb": digest(path.read_bytes())}}))
        return root, path, folder / marker
    return create


@pytest.mark.parametrize("mode", ["screen", "mature", "design"])
@pytest.mark.parametrize("engine", ["boltz2", "colabfold", "chai1", "opendde"])
def test_vhh_raw_download_is_exact_native_receipted_file(vhh_run, mode, engine):
    root, path, marker = vhh_run(mode, engine)
    before = inventory(root)
    reader = ResultReader(root)
    for observation_id in (None, "scored:a"):
        record = reader.structure("a", observation_id)
        assert record["content"] == record["raw_content"] == path.read_bytes().decode("utf-8")
        assert record["raw_format"] == "pdb"
        source = record["coordinate_source"]
        assert source["raw_preserved"] is True
        assert source["display_relation"] == "native_identical"
        assert source["raw_sha256"] == source["display_sha256"] == digest(path.read_bytes())
        assert source["verification"] == {"status": "verified", "basis": "native_completion_output_sha256",
            "receipt_path": str(marker.relative_to(root)), "receipt_sha256": digest(marker.read_bytes())}
    assert inventory(root) == before


@pytest.mark.parametrize("mode", ["screen", "mature", "design"])
def test_vhh_missing_or_corrupt_receipt_never_promotes_display_to_native(vhh_run, mode):
    root, path, marker = vhh_run(mode, receipt=False)
    record = ResultReader(root).structure("a")
    assert record["coordinate_source"]["verification"]["status"] == "unavailable"
    assert record["raw_content"] is None
    assert record["content"] == path.read_text()
    marker.write_text(json.dumps({"status": "complete", "outputs": {"model.pdb": "0" * 64}}))
    record = ResultReader(root).structure("a")
    assert record["coordinate_source"]["verification"]["status"] == "hash_mismatch"
    assert record["coordinate_source"]["raw_preserved"] is False
    assert record["content"] == path.read_text()


def test_native_download_preserves_crlf_while_display_contract_reports_difference(vhh_run):
    root, path, _ = vhh_run(crlf=True)
    record = ResultReader(root).structure("a")
    assert record["raw_content"].encode("utf-8") == path.read_bytes()
    assert record["content"] == path.read_text()  # Preserve the established display content.
    assert record["coordinate_source"]["display_relation"] == "transformed_display"
    assert record["coordinate_source"]["raw_sha256"] == digest(path.read_bytes())


@pytest.mark.parametrize("mode", ["complex", "screen", "mature", "design"])
@pytest.mark.parametrize("kind", ["identical", "transformed", "legacy"])
def test_retained_contract_has_the_same_origin_semantics_in_every_mode(tmp_path, monkeypatch, mode, kind):
    root = tmp_path / "run"
    root.mkdir()
    (root / "run_config.yaml").write_text(yaml.safe_dump({"run": {"mode": mode}}))
    raw = "HEADER preserved native\r\nEND\r\n"
    payload = {"candidate_id": "a", "content": raw if kind == "identical" else "HEADER saved display\nEND\n", "format": "pdb"}
    if kind != "legacy":
        payload.update(raw_content=raw, raw_format="pdb", coordinate_source={"raw_sha256": digest(raw.encode()),
            "display_transform": "recorded renumbering and alignment"})
    monkeypatch.setattr("nanocofold.platform.retention.retained_structure", lambda *args: copy.deepcopy(payload))
    reader = ResultReader(root)
    reader.retained = {"verified_snapshot": True}
    record = reader.structure("a")
    assert record["content"] == payload["content"]
    source = record["coordinate_source"]
    assert source["display_relation"] == {"identical": "native_identical", "transformed": "transformed_display", "legacy": "unverified_legacy"}[kind]
    assert source["raw_preserved"] is (kind != "legacy")
    assert record["raw_content"] == (raw if kind != "legacy" else None)


@pytest.mark.parametrize("failure", ["embedded_mismatch", "file_mismatch", "source_hash_conflict", "external_path", "missing_raw"])
def test_complex_bad_raw_proof_does_not_hide_valid_display(tmp_path, failure):
    root = tmp_path / "run"
    root.mkdir()
    (root / "run_config.yaml").write_text("run:\n  mode: complex\n")
    raw = b"HEADER native\r\nEND\r\n"
    path = root / "native.pdb"
    path.write_bytes(raw)
    source = {"raw_path": "native.pdb", "raw_sha256": digest(raw)}
    data = {"content": "HEADER transformed display\nEND\n", "format": "pdb", "raw_content": raw.decode(), "raw_format": "pdb",
            "coordinate_source": source, "pae": {"values": [[1]], "labels": ["A:1"]}, "chains": [{"id": "A"}]}
    row = {"candidate_id": "a", "structure_available": True, "structure_file": "structure.json", "artifacts": {"native.pdb": digest(raw)}}
    if failure == "embedded_mismatch": data["raw_content"] += "REMARK changed\n"
    if failure == "file_mismatch": path.write_bytes(raw + b"REMARK changed\n")
    if failure == "source_hash_conflict": row["artifacts"]["native.pdb"] = "0" * 64
    if failure == "external_path": source["raw_path"] = str(tmp_path / "outside.pdb")
    if failure == "missing_raw":
        path.unlink()
        data.pop("raw_content")
    (root / "structure.json").write_text(json.dumps(data))
    (root / "complex_results.json").write_text(json.dumps({"rows": [row]}))
    before = inventory(root)
    record = ResultReader(root).structure("a")
    assert record["raw_content"] is None
    assert record["coordinate_source"]["raw_preserved"] is False
    assert record["content"] == data["content"]
    assert record["pae"] == data["pae"] and record["chains"] == data["chains"]
    assert inventory(root) == before


def test_embedded_native_survives_removal_of_external_artifacts_after_retention(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    reader = ResultReader(root)
    raw = "HEADER preserved native\nEND\n"
    record = reader._coordinate_contract({"content": "HEADER display\nEND\n", "raw_content": raw, "raw_format": "pdb",
        "coordinate_source": {"raw_path": "removed/native.pdb", "raw_sha256": digest(raw.encode())}})
    assert record["raw_content"] == raw
    assert record["coordinate_source"]["verification"]["basis"] == "stored_coordinate_source_sha256"


def test_observation_scope_is_copied_only_from_the_same_scored_coordinate(vhh_run):
    root, path, _ = vhh_run()
    table = root / "03_score/scored_candidates.csv"
    table.write_text(table.read_text().replace("engine\n", "engine,iptm_scope,iptm_source\n").replace("boltz2\n", "boltz2,selected_target_binder_pair,recorded_pair_axis\n"))
    parent = root / "02_prediction"
    (parent / "predictions.csv").write_text(f"candidate_id,engine,pred_dir\na,boltz2,{path.parent.relative_to(root)}\n")
    reader = ResultReader(root)
    matched = reader.observations()["rows"][0]
    assert matched["iptm_scope"] == "selected_target_binder_pair"
    assert matched["iptm_source"] == "recorded_pair_axis"
    other = path.with_name("other.pdb")
    other.write_bytes(path.read_bytes())
    table.write_text(table.read_text().replace("model.pdb", "missing.pdb"))
    assert all("iptm_scope" not in row and "iptm_source" not in row for row in ResultReader(root).observations()["rows"])


def test_cloud_structure_json_keeps_verified_raw_utf8_bytes_and_hash(tmp_path, vhh_run):
    from nanocofold.platform.cloud_results import store_structure
    root, path, _ = vhh_run(crlf=True)
    record = ResultReader(root).structure("a")
    before = copy.deepcopy(record)
    destination = tmp_path / "cloud_copy"
    stored = store_structure(record, destination, "structures/a")
    loaded = json.loads((destination / stored["file"]).read_bytes().decode("utf-8"))
    assert loaded["raw_content"].encode("utf-8") == path.read_bytes()
    assert digest(loaded["raw_content"].encode("utf-8")) == loaded["coordinate_source"]["raw_sha256"]
    assert (destination / stored["model_file"]).read_bytes() == record["content"].encode("utf-8")
    assert record == before  # Tiling a cloud copy must not mutate the API payload.


def test_external_completion_receipt_cannot_hide_display_or_expose_raw(vhh_run, tmp_path):
    root, path, marker = vhh_run(receipt=False)
    outside = tmp_path / "foreign-receipt.json"
    outside.write_text(json.dumps({"status": "complete", "outputs": {"model.pdb": digest(path.read_bytes())}}))
    marker.symlink_to(outside)
    record = ResultReader(root).structure("a")
    assert record["content"] == path.read_text()
    assert record["raw_content"] is None
    assert record["coordinate_source"]["verification"]["status"] == "unavailable"
