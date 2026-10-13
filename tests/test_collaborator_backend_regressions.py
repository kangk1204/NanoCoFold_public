"""Collaborator audit regressions exercised through input and output boundaries."""
from __future__ import annotations

import copy
import csv
import io
import json
import base64
import gzip
import hashlib
import shutil
import struct
import subprocess
import zipfile

import pandas as pd
import pytest
import yaml

from nanocofold import mature, report
from nanocofold.config import cfg_set, load_config
from nanocofold.design_benchmark_report import _text_data
from nanocofold.platform.inputs import InputManager, normalized_config, parse_fasta
from nanocofold.platform.results import ResultReader
from nanocofold.publication import gate_summary
from nanocofold.utils.atomic import write_csv
from nanocofold.utils.presentation import portable_metadata


@pytest.fixture
def cfg(tmp_path):
    """Self-contained so this regression file also runs in the public source."""
    config = load_config()
    config["run"]["mock"] = True
    config["run"]["outdir"] = str(tmp_path / "runs")
    return config


@pytest.mark.parametrize("path", ["design.framework", "design.env", "score.ipsae.official_script",
                                  "structure.msa.mmseqs_binary", "structure.msa.colabfold_search",
                                  "mature.anarci.env", "mature.antifold.model_path"])
def test_browser_request_cannot_register_an_external_tool_path(tmp_path, path):
    override = {}
    cfg_set(override, path, "/etc/hosts")
    with pytest.raises(ValueError, match="작업 공간"):
        normalized_config(tmp_path, override)


def test_registered_external_runtime_is_allowed_but_relative_paths_use_workspace(tmp_path):
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / "environment.yaml").write_text("mature:\n  antifold:\n    model_path: /registered/models/model.pt\n")
    cfg = normalized_config(tmp_path, {"mature": {"antifold": {"model_path": "/registered/models/model.pt"}},
                                      "score": {"ipsae": {"official_script": "tools/ipsae.py"}}})
    assert cfg["mature"]["antifold"]["model_path"] == "/registered/models/model.pt"
    assert cfg["score"]["ipsae"]["official_script"] == str(tmp_path / "tools/ipsae.py")


@pytest.mark.parametrize("length", [2001, 100000])
def test_target_residue_budget_is_checked_before_review_artifact_creation(tmp_path, length):
    manager = InputManager(tmp_path)
    try:
        with pytest.raises(ValueError, match="2,?000잔기"):
            manager.fasta({"content": ">target\n" + "A" * length, "mock": True})
        assert manager.list() == []
    finally:
        manager.close()


def test_raw_amino_acid_paste_remains_supported_and_casefold_collision_fails():
    assert parse_fasta("ACDEFGHIKL", single=True) == [("sequence", "ACDEFGHIKL")]
    with pytest.raises(ValueError, match="대소문자"):
        parse_fasta(">Candidate\nACDE\n>candidate\nFGHI")
    with pytest.raises(ValueError):
        parse_fasta(">candidate\nACD123E")


@pytest.mark.parametrize("setting,value", [("score.boltz2_diffusion_samples", 2.9),
    ("score.boltz2_diffusion_samples", -5), ("score.boltz2_diffusion_samples", "abc"),
    ("screen.num_diffn_samples", 0), ("rank.final_n", 0), ("truncate.radius", 0),
    ("structure.boltz2.diffusion_samples", 100000000),
    ("mature.prefilter.percentile", 101), ("mature.prefilter.percentile", 0),
    ("mature.prefilter.threshold_mode", "percentlie")])
def test_invalid_counts_and_prefilter_settings_fail_at_config_loading(setting, value):
    with pytest.raises(ValueError, match=setting):
        load_config(overrides=[f"{setting}={json.dumps(value)}"])


def test_supported_analysis_options_do_not_emit_typo_warnings():
    with __import__("warnings").catch_warnings(record=True) as captured:
        cfg = load_config(overrides=["score.boltz2_diffusion_samples=2", "mature.antifold.model_path=models/a.pt",
                                     "screen.cascade.calibrate_wt=true", "screen.cascade.prune_unselected=false"])
    assert not captured
    assert cfg["score"]["boltz2_diffusion_samples"] == 2


def test_failed_gate_missing_or_invalid_counts_are_not_manufactured_zeroes():
    assert gate_summary({"ok": False})["n_errors"] is None
    assert gate_summary({"ok": False, "n_errors": -1})["n_errors"] is None
    assert gate_summary({"ok": False, "n_errors": 0, "n_warnings": 0})["diagnostic"]


def test_report_uses_saved_thresholds_and_reports_corrupt_hotspot_table(tmp_path, cfg):
    cfg["report"]["viewer"] = "none"
    saved = copy.deepcopy(cfg)
    saved["score"]["thresholds"]["ipsae"] = .6
    (tmp_path / "run_config.yaml").write_text(yaml.safe_dump(saved))
    cfg["score"]["thresholds"]["ipsae"] = .95
    target = tmp_path / "01_target"
    target.mkdir()
    (target / "hotspot_table.csv").write_text('label,aa\n"unterminated')
    frame = pd.DataFrame([{"design_id": "candidate", "sequence": "ACDE", "ipsae": .7,
                           "final_evaluated": True, "final_eligible": True, "pass_ipsae": True}])
    original = frame.copy(deep=True)
    document = report.generate_report(tmp_path, cfg, {}, scored=frame, selected=frame).read_text()
    assert "&gt;= 0.6" in document
    assert "0.95" not in document
    assert "hotspot_table.csv을 읽지 못했습니다" in document
    assert "해당 핫스팟 표는 미평가" in document
    pd.testing.assert_frame_equal(frame, original)
    assert saved == yaml.safe_load((tmp_path / "run_config.yaml").read_text())


def test_per_model_threshold_receipts_override_saved_run_default(tmp_path, cfg):
    cfg["report"]["viewer"] = "none"
    (tmp_path / "run_config.yaml").write_text(yaml.safe_dump(cfg))
    frame = pd.DataFrame([{"design_id": "candidate", "threshold_checks": [{"metric": "ipsae", "value": .7,
        "operator": ">=", "threshold": .65, "passed": True, "threshold_source": "scoring_receipt"}]}])
    document = report.generate_report(tmp_path, cfg, {}, scored=frame).read_text()
    assert "&gt;= 0.65" in document and "scoring_receipt" in document


def test_selected_list_limit_and_planned_model_count_are_visible(tmp_path, cfg):
    cfg["report"]["viewer"] = "none"
    folder = tmp_path / "02_comparison"
    folder.mkdir()
    (folder / "comparison_status.json").write_text(json.dumps({"expected_rows": 64}))
    frame = pd.DataFrame([{"design_id": f"d{i}", "sequence": "ACDE", "model_record": True, "evaluated": True} for i in range(60)])
    document = report.generate_report(tmp_path, cfg, {}, scored=frame, selected=frame).read_text()
    assert "표시 50 / 전체 60 후보·모델 기록" in document
    assert "계획 모델 64건 · 실제 모델 기록 60건" in document


def test_delivery_provenance_includes_saved_versions_and_runtime_identity(tmp_path):
    (tmp_path / "run_config.yaml").write_text(yaml.safe_dump({"run": {"mode": "screen"}, "screen": {"comparison": {"enabled": True}}}))
    comparison = tmp_path / "02_comparison"
    comparison.mkdir()
    gpu_uuid = "GPU-12345678-1234-1234-1234-123456789abc"
    identity = {"runtime": {"boltz2": {"engine": "boltz2", "source": {"/registered/pkg/file.py": "f" * 64}, "weights": [],
        "environment": {"CUDA_VISIBLE_DEVICES": gpu_uuid}}}}
    (comparison / "comparison_identity.json").write_text(json.dumps(identity))
    (tmp_path / "preflight.json").write_text(json.dumps({"engines": {"boltz2": {
        "env_probe": {"imports": {"boltz": {"version": "2.2.1"}, "torch": {"version": "2.14.0"}}}}}}))
    reader = ResultReader(tmp_path)
    provenance = reader.provenance()
    assert provenance["engine_provenance"][0]["versions"]["boltz"] == "2.2.1"
    assert provenance["engine_provenance"][0]["runtime"]["source_files"] == [{"path": "/registered/pkg/file.py", "sha256": "f" * 64}]
    assert "source" in identity["runtime"]["boltz2"]  # Source receipt remains unchanged.
    original_identity = (comparison / "comparison_identity.json").read_bytes()
    assert portable_metadata(provenance, root=tmp_path)["engine_provenance"][0]["runtime"]["source_files"][0]["path"] == "[local path]"
    assert "02_comparison/comparison_identity.json" in {x["path"] for x in provenance["source_artifacts"]}
    data, _, _ = reader.export("report_zip")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        manifest = json.loads(archive.read("delivery_manifest.json"))
        delivered = manifest["receipt"]["provenance"]
        assert delivered["engine_provenance"][0]["versions"]["boltz"] == "2.2.1"
        assert delivered["engine_provenance"][0]["runtime"]["source_files"][0]["path"] == "[local path]"
        assert "02_comparison/comparison_identity.json" in {x["path"] for x in delivered["source_artifacts"]}
        assert b"/registered/pkg/file.py" not in archive.read("report.html")
        for name in archive.namelist():
            assert gpu_uuid.encode() not in archive.read(name)
        assert delivered["engine_provenance"][0]["runtime"]["environment"]["CUDA_VISIBLE_DEVICES"] == "[GPU UUID]"
    assert (comparison / "comparison_identity.json").read_bytes() == original_identity


def test_portable_gpu_uuid_masks_nested_metadata_without_changing_native_payloads():
    gpu_uuid = "GPU-12345678-1234-1234-1234-123456789abc"
    mig_uuid = "MIG-87654321-4321-4321-4321-cba987654321"
    value = {"environment": {"CUDA_VISIBLE_DEVICES": gpu_uuid + ",0"},
             "nested_json": json.dumps({"device": mig_uuid}),
             "note": "selected " + gpu_uuid,
             "job_id": "12345678-1234-1234-1234-123456789abc",
             "content": "REMARK " + gpu_uuid,
             "sha256": "f" * 64, "ordinal": "0"}
    delivered = portable_metadata(value)
    assert delivered["environment"]["CUDA_VISIBLE_DEVICES"] == "[GPU UUID],0"
    assert json.loads(delivered["nested_json"])["device"] == "[GPU UUID]"
    assert delivered["note"] == "selected [GPU UUID]"
    for key in ("job_id", "content", "sha256", "ordinal"):
        assert delivered[key] == value[key]
    assert value["environment"]["CUDA_VISIBLE_DEVICES"] == gpu_uuid + ",0"


def test_host_root_shaped_untyped_base64_is_preserved_but_explicit_path_redacted():
    value = "/home/" + "A" * 66
    assert portable_metadata({"data": value})["data"] == value
    assert portable_metadata({"path": value})["path"] == "[local path]"


def test_benchmark_and_atomic_csv_escape_formulas_without_changing_input(tmp_path):
    frame = pd.DataFrame([{"id": "=SUM(1,2)", "metric": -.25}, {"id": "variant-1", "metric": .2}])
    original = frame.copy(deep=True)
    path = tmp_path / "models.csv"
    write_csv(path, frame)
    rows = list(csv.DictReader(path.open()))
    assert rows[0]["id"] == "'=SUM(1,2)" and rows[0]["metric"] == "-0.25"
    assert rows[1]["id"] == "variant-1"
    pd.testing.assert_frame_equal(frame, original)
    assert list(csv.DictReader(io.StringIO(_text_data(frame.to_dict("records")).decode("utf-8-sig"))))[0]["id"].startswith("'")


def test_antifold_refuses_ambiguous_output_csvs(tmp_path):
    for name in ["old.csv", "current.csv"]:
        pd.DataFrame([{"pdb_posins": "1", "pdb_chain": "H", "A": -1.}]).to_csv(tmp_path / name, index=False)
    messages = []
    assert mature._parse_antifold_output(tmp_path, {("H", "1"): 1}, {}, messages) is None
    assert messages and "여러 개" in messages[0]


def test_unknown_config_warning_is_in_saved_browser_review(tmp_path):
    manager = InputManager(tmp_path)
    try:
        with pytest.warns(UserWarning, match="typo_option"):
            saved = manager.fasta({"content": "ACDEFGHIKL", "mock": True,
                                  "config": {"score": {"typo_option": 1}}})
        assert any("typo_option" in warning for warning in saved["review"]["warnings"])
        assert saved["review"]["source"]["kind"] == "raw_amino_acid_paste"
        assert saved["review"]["source"]["assigned_sequence_id"] == "sequence"
    finally:
        manager.close()


@pytest.mark.parametrize("case", ["valid", "wrong_hash", "wrong_size", "missing_crypto"])
def test_design_report_pae_checks_actual_decoded_sha256(case):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to exercise the shipped browser PAE function")
    from nanocofold.design_benchmark_report import _DOCUMENT
    function = next(line for line in _DOCUMENT.splitlines() if line.startswith("async function paePlot("))
    raw = struct.pack("<4d", 1., 2., 3., 4.)
    entry = {"data": base64.b64encode(gzip.compress(raw)).decode(), "bytes": len(raw), "dimension": 2,
             "sha256": hashlib.sha256(raw).hexdigest(), "labels": ["A:1", "B:1"]}
    if case == "wrong_hash":
        entry["sha256"] = "0" * 64
    if case == "wrong_size":
        entry["bytes"] += 8
    source = "import {webcrypto} from 'node:crypto';\n"
    source += "Object.defineProperty(globalThis,'crypto',{value:" + ("undefined" if case == "missing_crypto" else "webcrypto") + ",configurable:true});\n"
    source += "const data=" + json.dumps({"pae_arrays": {"test": entry}}) + ";let serial=1,plots=0;const note={textContent:''};const $=()=>note;const Plotly={purge(){},newPlot(){plots++;}};\n"
    source += function + "\nawait paePlot({pae_key:'test'},1);console.log(JSON.stringify({plots,note:note.textContent}));"
    result = subprocess.run([node, "--input-type=module", "-e", source], capture_output=True, text=True, check=True)
    outcome = json.loads(result.stdout)
    assert outcome["plots"] == (1 if case == "valid" else 0)
    if case == "wrong_hash":
        assert "SHA256 불일치" in outcome["note"]
    elif case == "wrong_size":
        assert "크기 불일치" in outcome["note"]
    elif case == "missing_crypto":
        assert "최신 브라우저" in outcome["note"]
