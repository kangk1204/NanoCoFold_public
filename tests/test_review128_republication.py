"""Re-scoring must preserve the prediction source, values and recorded selection."""
import hashlib
import json

import pandas as pd
import pytest
import yaml

from nanocofold import cli
from nanocofold.state import RunState
from nanocofold.utils.predictions import prediction_attempt, record_manifest, discard_manifest, attempt_manifests
from audit121_fixtures import small_config, VHH


def run_config(tmp_path, case):
    mode = case if case in {"design", "mature"} else "screen"
    cfg = small_config(mode)
    cfg["screen"].update(engine="colabfold", tier2_n=2, restraint={"enabled": False})
    cfg["design"] = {"num_designs": 2, "mpnn": {"seqs_per_struct": 1},
                     "filters": {"enabled": False, "top_n_to_score": 2}}
    cfg["mature"]["library"] = {"max_variants": 2}
    cfg["mature"]["prefilter"]["top_n"] = 2
    cfg["mature"]["antifold"] = {"enabled": False}
    library = tmp_path / "library.fasta"
    library.write_text(f">first\n{VHH}\n>second\n{VHH[:-1]}A\n")
    cfg["screen"]["vhh_fasta"] = str(library)
    if case in {"chai1", "rescore"}:
        cfg["screen"]["engine"] = "chai1"
    if case == "rescore":
        cfg["screen"]["boltz2_rescore_top"] = 2
    if case == "comparison":
        cfg["screen"]["comparison"] = {"enabled": True, "engines": ["colabfold", "boltz2", "chai1", "opendde"]}
    if case == "cascade":
        cfg["screen"]["cascade"] = {"enabled": True, "final_count": 2, "audit_n": 0, "diversity_n": 0}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return mode, path


def published(run):
    scored = pd.read_csv(run / "03_score/scored_candidates.csv", dtype={"design_id": str})
    # Predictor aliases were historically omitted by score. Compare the actual
    # engine, source and measured/decision columns rather than incidental schema.
    fields = [c for c in ("design_id", "engine", "pred_dir", "cif_path", "iptm", "ipsae",
                         "interface_pae", "ranking_status", "final_eligible", "final_evaluated",
                         "selection_rank", "exclusion_reason") if c in scored]
    return scored[fields].sort_values("design_id").reset_index(drop=True)


@pytest.mark.parametrize("case", ["screen", "design", "mature", "comparison", "chai1", "cascade", "rescore"])
def test_score_twice_preserves_prediction_and_selection(tmp_path, case):
    mode, config = run_config(tmp_path, case)
    run = tmp_path / "run"
    assert cli.main([mode, "--config", str(config), "--run-dir", str(run), "--mock"]) == 0
    before = published(run)
    selected = pd.read_csv(run / "05_final/selected_candidates.csv").get("design_id", pd.Series(dtype=str)).tolist()
    state = json.loads((run / "run_state.json").read_text())
    records = state["prediction_stages"]["manifests"]
    assert records, "The completed prediction must have a stage receipt"
    for item in records:
        path = run / item["path"]
        assert path.is_file(), item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    for _ in range(2):
        assert cli.main(["score", "--run-dir", str(run)]) == 0
        pd.testing.assert_frame_equal(published(run), before)
        assert pd.read_csv(run / "05_final/selected_candidates.csv").get("design_id", pd.Series(dtype=str)).tolist() == selected
    assert cli.main(["report", "--run-dir", str(run)]) == 0


def test_empty_receipt_does_not_republish_older_or_design_stages(tmp_path):
    cfg = small_config("design")
    stage = tmp_path / "02b_tier2/predictions.csv"
    stage.parent.mkdir()
    stage.write_text("design_id,engine,pred_dir\nold,colabfold,old\n")
    state = RunState(tmp_path)
    with prediction_attempt(tmp_path):
        record_manifest(stage)
        cli._persist_prediction_stages(state, cfg)
    # A subsequent producer forgets to record its output. Keeping the old
    # receipt or guessing from files would incorrectly reuse the previous run.
    with prediction_attempt(tmp_path):
        cli._persist_prediction_stages(state, cfg)
    with pytest.raises(ValueError, match="기록이 비어"):
        cli._load_prediction_manifests(tmp_path, cfg)
    assert stage.read_text().endswith("old,colabfold,old\n")


def test_unexpected_missing_manifest_stays_in_receipt(tmp_path):
    path = tmp_path / "predictions.csv"
    path.write_text("data")
    with prediction_attempt(tmp_path):
        record_manifest(path)
        path.unlink()
        assert attempt_manifests(tmp_path) == [{"path": "predictions.csv", "sha256": hashlib.sha256(b"data").hexdigest()}]
        discard_manifest(path)
        assert attempt_manifests(tmp_path) == []


@pytest.mark.parametrize("failure", ["receipt", "during_publication"])
def test_failed_score_preserves_only_unchanged_completed_publication(tmp_path, monkeypatch, failure):
    mode, config = run_config(tmp_path, "chai1")
    run = tmp_path / "run"
    assert cli.main([mode, "--config", str(config), "--run-dir", str(run), "--mock"]) == 0
    before = published(run)
    old_execution = RunState(run).data["execution"]
    if failure == "receipt":
        state = RunState(run)
        state.data["prediction_stages"]["manifests"][0]["sha256"] = "0" * 64
        state.save()
    else:
        def fail(*args, **kwargs):
            raise RuntimeError("injected score failure after publication checkpoint")
        monkeypatch.setattr(cli, "_score_candidates", fail)
    assert cli.main(["score", "--run-dir", str(run)]) == 1
    state = RunState(run).data
    pd.testing.assert_frame_equal(published(run), before)
    if failure == "receipt":
        assert state["execution"] == old_execution
        assert state["score_attempt"]["state"] == "rejected"
        assert cli.main(["report", "--run-dir", str(run)]) == 0
    else:
        assert state["execution"]["state"] == "failed"
        assert state["phases"]["final"]["tables_committed"] is False
        assert cli.main(["report", "--run-dir", str(run)]) == 1


@pytest.mark.parametrize("engine", ["chai1", "colabfold"])
def test_old_cascade_stage_cannot_be_enabled_by_a_score_option(tmp_path, engine):
    mode, config = run_config(tmp_path, "cascade")
    run = tmp_path / "run"
    assert cli.main([mode, "--config", str(config), "--run-dir", str(run), "--mock"]) == 0
    old_path = run / "02c_boltz2/predictions.csv"
    old_hash = hashlib.sha256(old_path.read_bytes()).hexdigest()
    assert cli.main([mode, "--run-dir", str(run), "--mock", "--set", "screen.cascade.enabled=false",
                     "--set", "screen.engine=" + engine, "--set", "screen.boltz2_rescore_top=0"]) == 0
    before = published(run)
    assert before.engine.eq(engine).all()
    for value in (2, 0):
        assert cli.main(["score", "--run-dir", str(run), "--set", f"screen.boltz2_rescore_top={value}"]) == 0
        pd.testing.assert_frame_equal(published(run), before)
    assert hashlib.sha256(old_path.read_bytes()).hexdigest() == old_hash
