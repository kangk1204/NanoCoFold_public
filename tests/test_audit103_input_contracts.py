"""Reviewed input contracts: no inference, workers, or network requests."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import warnings

import pytest
import yaml

from nanocofold.config import PROJECT_ROOT, exploration_config, load_config
from nanocofold.platform.batches import BatchManager, expand_manifest
from nanocofold.platform.inputs import InputManager, normalized_config, parse_fasta
from nanocofold.platform.jobs import JobManager
from nanocofold.utils.seq import validate_candidate_id


@pytest.mark.parametrize("identifier", ["=cmd_variant", "+v1", "-v1", "@v1"])
@pytest.mark.parametrize("single", [False, True])
def test_reviewed_fasta_uses_pipeline_formula_id_diagnostic(identifier, single):
    with pytest.raises(ValueError) as pipeline_error:
        validate_candidate_id(identifier)
    with pytest.raises(ValueError) as review_error:
        parse_fasta(f">{identifier} description\nQVQLVESGGG\n", single=single)
    assert str(review_error.value) == str(pipeline_error.value)


@pytest.mark.parametrize("identifier", ["007", "variant-1", "variant+1", "A@B", "WT"])
def test_reviewed_fasta_preserves_ordinary_ids_and_header_tokens(identifier):
    assert parse_fasta(f">{identifier} description\nqvqlvesggg\n") == [(identifier, "QVQLVESGGG")]


@pytest.mark.parametrize("identifier", ["=cmd_variant", "+v1", "-v1", "@v1"])
@pytest.mark.parametrize("field,mode", [("vhh_fasta", "screen")])
def test_batch_manifest_rejects_formula_ids_in_each_fasta_input(identifier, field, mode):
    payload = {"version": 1, "mode": mode, "mock": True,
               "entries": [{"id": "review", "content": ">target\nACDEFGHI\n"}],
               "vhh_fasta": ">binder\nQVQLVESGGG\n", "wt_fasta": ">WT\nQVQLVESGGG\n"}
    destination = payload["entries"][0] if field == "content" else payload
    destination[field] = f">{identifier}\nQVQLVESGGG\n"
    original = copy.deepcopy(payload)
    with pytest.raises(ValueError, match="스프레드시트 수식 시작 문자"):
        expand_manifest(payload)
    assert payload == original



@pytest.mark.parametrize("identifier", ["=reference", "+reference", "-reference", "@reference", "sp|P42212|GFP_AEQVI/1-238"])
@pytest.mark.parametrize("field,mode", [("content", "design"), ("wt_fasta", "mature")])
def test_reference_header_labels_are_distinct_from_candidate_and_batch_ids(identifier, field, mode):
    payload = {"version": 1, "mode": mode, "mock": True,
               "entries": [{"id": "review", "content": ">target\nACDEFGHI\n"}],
               "wt_fasta": ">WT\nQVQLVESGGG\n"}
    destination = payload["entries"][0] if field == "content" else payload
    destination[field] = f">{identifier} description\nQVQLVESGGG\n"
    original = copy.deepcopy(payload)
    expanded = expand_manifest(payload)
    assert expanded[0]["input"][field] == destination[field]
    assert payload == original
    # Count validation never bypasses safeguards when this same header is used
    # as a candidate identity or as the source of an independent batch ID.
    for single in (False, True):
        with pytest.raises(ValueError):
            parse_fasta(destination[field], single=single)
    with pytest.raises(ValueError):
        expand_manifest({"version": 1, "mode": "design", "targets_fasta": destination[field], "mock": True})


def test_batch_review_rejects_formula_library_before_saving_inputs_or_jobs(tmp_path):
    inputs, jobs = InputManager(tmp_path), JobManager(tmp_path)
    try:
        batches = BatchManager(inputs, jobs)
        with pytest.raises(ValueError, match="스프레드시트 수식 시작 문자"):
            batches.review({"version": 1, "mode": "screen", "mock": True,
                            "targets_fasta": ">target\nACDEFGHI\n",
                            "vhh_fasta": ">=cmd_variant\nQVQLVESGGG\n"})
        assert inputs.list() == []
        assert jobs.store.list() == []
        assert batches.list() == []
        assert not list(inputs.root.iterdir())
    finally:
        jobs.close()
        inputs.close()


def test_actual_batch_review_cli_rejects_formula_library_without_predictions(tmp_path):
    source = Path(__file__).resolve().parents[1]
    manifest = tmp_path / "batch.json"
    manifest.write_text(json.dumps({"version": 1, "mode": "screen", "mock": True,
                                   "targets_fasta": ">target\nACDEFGHI\n",
                                   "vhh_fasta": ">=cmd_variant\nQVQLVESGGG\n"}))
    workspace = tmp_path / "workspace"
    environment = {**os.environ, "PYTHONPATH": str(source)}
    environment.pop("NANOCOFOLD_ROOT", None)
    result = subprocess.run([sys.executable, "-m", "nanocofold", "batch", "review",
                             "--manifest", str(manifest), "--workspace", str(workspace)],
                            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "스프레드시트 수식 시작 문자" in result.stderr
    assert not list(workspace.rglob("platform_input.json"))
    assert not list(workspace.rglob("run_state.json"))
    assert not list(workspace.rglob("manifest.json"))


@pytest.mark.parametrize("value", [[], {"a": 1}, 7, True, False, float("nan")])
@pytest.mark.parametrize("source", ["override", "yaml", "platform"])
def test_analysis_preset_rejects_non_string_values_with_setting_name(tmp_path, value, source):
    with pytest.raises(ValueError, match="run.analysis_preset"):
        if source == "override":
            scalar = yaml.safe_dump(value, default_flow_style=True).split("\n...")[0].strip()
            load_config(overrides=["run.analysis_preset=" + scalar])
        elif source == "yaml":
            path = tmp_path / "config.yaml"
            path.write_text(yaml.safe_dump({"run": {"analysis_preset": value}}))
            load_config(path)
        else:
            normalized_config(tmp_path, {"run": {"analysis_preset": value}})


@pytest.mark.parametrize("value", [None, "", "researcher_exploration", "custom/review", "  user label  "])
def test_analysis_preset_preserves_nullable_string_labels(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"run": {"analysis_preset": value}}))
    assert load_config(path)["run"]["analysis_preset"] == value
    assert normalized_config(tmp_path, {"run": {"analysis_preset": value}})["run"]["analysis_preset"] == value


def test_exploration_preset_keeps_all_documented_conditions_and_no_warning():
    preset = exploration_config()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        resolved = load_config(PROJECT_ROOT / "config/researcher_exploration.yaml")
    def assert_preserved(expected, actual):
        for key, value in expected.items():
            if isinstance(value, dict):
                assert_preserved(value, actual[key])
            else:
                assert actual[key] == value
    assert_preserved(preset, resolved)
    assert not any("analysis_preset" in str(item.message) for item in caught)
