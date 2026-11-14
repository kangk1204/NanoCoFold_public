"""Remaining A–F review contracts, with isolated CPU-only inputs."""
import copy
import json
import warnings
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from nanocofold import cli
from nanocofold.config import cfg_seed, dump_resolved, load_config


SEEDS = ("run.seed", "structure.boltz2.seed", "structure.chai1.seed", "score.boltz2.seed",
         "mature.library.seed", "screen.cascade.audit_seed")


@pytest.mark.parametrize("key", SEEDS)
def test_new_execution_seeds_share_web_and_complex_range(key):
    cfg = load_config(overrides=[f"{key}={2**31-1}"])
    assert cfg_seed(cfg, key) == 2**31-1
    for value in (2**31, 2**32-1):
        with pytest.raises(ValueError, match="0–2147483647"):
            load_config(overrides=[f"{key}={json.dumps(value)}"])
    for value in (-1, True):
        with pytest.raises(ValueError, match=key.replace('.', r'\.')):
            load_config(overrides=[f"{key}={json.dumps(value)}"])


def test_historical_uint32_seed_is_read_without_rewriting_or_permitting_resubmission(tmp_path):
    from nanocofold.platform.results import ResultReader
    cfg = load_config()
    cfg["run"].update(mode="screen", seed=2**32-1)
    dump_resolved(cfg, tmp_path/"run_config.yaml")
    before = (tmp_path/"run_config.yaml").read_bytes()
    historical = load_config(tmp_path/"run_config.yaml", historical=True)
    assert cfg_seed(historical, "score.boltz2.seed", historical=True) == 2**32-1
    assert ResultReader(tmp_path)._row_conditions({"engine": "boltz2"})["seed"] == 2**32-1
    args = SimpleNamespace(set=[], mode=None, command="report", config=None, run_dir=str(tmp_path), mock=False, dry_run=False)
    assert cli._load(args)["run"]["seed"] == 2**32-1
    args.command = "run"
    with pytest.raises(ValueError, match="2147483647"):
        cli._load(args)
    assert (tmp_path/"run_config.yaml").read_bytes() == before


def inactive_warnings(overrides):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load_config(overrides=overrides)
    return [str(item.message) for item in caught if "선택한 엔진에서 사용하지 않는 설정:" in str(item.message)]


@pytest.mark.parametrize("option,active,inactive", [
    ('screen.extra_args=["--no-low-memory"]', "chai1", "boltz2"),
    ('score.boltz2_extra_args=["--step_scale","1.5"]', "boltz2", "chai1"),
    ("screen.num_trunk_recycles=3", "chai1", "colabfold"),
    ('screen.env_vars={OMP_NUM_THREADS: "4"}', "boltz2", "opendde"),
    ("screen.colabfold.num_models=2", "colabfold", "chai1"),
    ("screen.opendde.cycles=3", "opendde", "boltz2"),
])
def test_engine_only_changed_options_warn_only_when_unused(option, active, inactive):
    base = ["run.mode=screen", "screen.restraint.enabled=false", option]
    assert option.split("=")[0] in " ".join(inactive_warnings(base + [f"screen.engine={inactive}"]))
    assert inactive_warnings(base + [f"screen.engine={active}"]) == []


def test_shared_msa_installed_defaults_comparison_rescore_and_specificity_are_not_mislabelled():
    assert inactive_warnings([]) == []
    assert inactive_warnings(["screen.engine=boltz2", "screen.colabfold.msa_max_sequences=128"]) == []
    option = 'score.boltz2_extra_args=["--step_scale","1.5"]'
    for overrides in (
        ["run.mode=screen", "screen.comparison.enabled=true"],
        ["run.mode=screen", "screen.engine=colabfold", "screen.restraint.enabled=false", "screen.cascade.enabled=true"],
        ["screen.boltz2_rescore_top=1"],
        ["specificity.enabled=true", "specificity.engine=boltz2"],
    ):
        assert inactive_warnings(overrides+[option]) == []


def test_rf_design_options_warn_when_only_boltzgen_is_selected():
    option = "design.mpnn.temperature=0.3"
    assert "design.mpnn" in " ".join(inactive_warnings(["design.engine=boltzgen", option]))
    assert inactive_warnings(["design.engine=both", option]) == []
    assert inactive_warnings(["design.engine=rfantibody", option]) == []


def test_inactive_option_warning_is_printed_and_saved_with_run(tmp_path, capsys):
    args = SimpleNamespace(set=["screen.engine=boltz2", 'screen.extra_args=["--no-low-memory"]'],
                           mode="screen", command="run", config=None, run_dir=str(tmp_path/"run"),
                           run_id=None, mock=True, dry_run=False)
    cfg = cli._load(args)
    _, state = cli._open_run(cfg, args)
    assert any("screen.extra_args" in value for value in state.warnings)
    assert any("screen.extra_args" in value for value in json.loads((tmp_path/"run/run_state.json").read_text())["warnings"])
    assert "[경고]" in capsys.readouterr().out


def test_threshold_mapping_warning_preserves_replacement_and_dotted_update_semantics():
    default = load_config()["score"]["thresholds"]
    with pytest.warns(UserWarning, match="임계값 목록 전체를 교체") as caught:
        cfg = load_config(overrides=['score.thresholds={ipsae: 0.6}'])
    assert cfg["score"]["thresholds"] == {"ipsae": .6}
    message = str(caught[0].message)
    assert all(key in message for key in set(default)-{"ipsae"})
    with warnings.catch_warnings(record=True) as caught:
        cfg = load_config(overrides=["score.thresholds.ipsae=0.6"])
    assert cfg["score"]["thresholds"] == dict(default, ipsae=.6)
    assert not caught


@pytest.mark.parametrize("error", [None, "", np.nan])
def test_nine_candidate_cascade_stops_instead_of_shrinking_to_audit_controls(error):
    from nanocofold.cascade import select_candidates
    cfg = load_config(overrides=["run.mode=screen", "screen.engine=colabfold", "screen.restraint.enabled=false",
                                 "screen.cascade.enabled=true", "screen.cascade.top_n=4", "screen.cascade.audit_n=2"])
    cfg["target"]["aux_chains"] = [{"name": "partner", "sequence": "ACDE"}]
    base = pd.DataFrame([{"design_id": name, "sequence": "ACDE"} for name in ["WT", *[f"v{i}" for i in range(8)]]])
    scores = base.assign(iptm=np.linspace(.5, .9, 9), ipsae=np.linspace(.4, .8, 9), error=error)
    assert select_candidates(base, scores, cfg).selected_for_boltz.sum() == 7
    scores["iptm"] = np.nan
    before = scores.copy(deep=True)
    with pytest.raises(ValueError, match="모든 성공 후보에서 미평가"):
        select_candidates(base, scores, cfg)
    pd.testing.assert_frame_equal(before, scores)
    explicit = copy.deepcopy(cfg)
    explicit["screen"]["cascade"]["ranking_metric"] = "ipsae"
    assert select_candidates(base, scores, explicit).selected_for_boltz.sum() == 7
    assert cfg["screen"]["cascade"]["ranking_metric"] == "iptm"


@pytest.mark.parametrize("mode", ["complex", "screen", "mature", "design"])
def test_shipped_batch_cli_review_needs_no_external_consent_or_jobs(tmp_path, mode, capsys):
    from nanocofold.install import init_workspace
    from nanocofold.platform.jobs import JobManager
    init_workspace(tmp_path)
    manifest = Path(__file__).resolve().parents[1]/"examples/tutorial"/(mode+"_batch.json")
    before = manifest.read_bytes()
    assert cli.main(["batch", "review", "--manifest", str(manifest), "--workspace", str(tmp_path)]) == 0
    batch = json.loads(capsys.readouterr().out)
    assert not any(row.get("error") for row in batch["rows"])
    assert batch["predictions_executed"] is False
    manager = JobManager(tmp_path)
    try:
        assert manager.store.list() == []
    finally:
        manager.close()
    assert manifest.read_bytes() == before


@pytest.mark.parametrize("final_count,parent_control,expected", [(None, False, 8), (None, True, 9), (4, False, 4)])
def test_cascade_eta_keeps_default_null_and_counts_the_actual_budget(tmp_path, final_count, parent_control, expected):
    from nanocofold.platform.eta import RuntimeEstimator
    stage = tmp_path/"02_screen"
    stage.mkdir()
    ids = ["WT", *[f"v{i}" for i in range(9)]]
    def write(folder, name, value):
        (folder/name).write_text(json.dumps(value))
    write(stage, "prediction_plan.json", {"compact": True, "fingerprint": "current", "candidate_ids": ids, "engine": "colabfold"})
    write(stage, "prediction_progress.json", {"fingerprint": "current", "completed": ids[:2]})
    write(stage, "compact_progress.json", {"fingerprint": "current", "batches": [{"wall_seconds": 20, "attempted": 2, "completed": 2}]})
    pd.DataFrame([{"design_id": name, "is_parent_control": parent_control and name == "v0"} for name in ids]).to_csv(stage/"candidates.csv", index=False)
    (tmp_path/"03_score").mkdir()
    write(tmp_path/"03_score", "cascade_calibration.json", {"valid": True, "mock": False, "seconds_per_candidate": 30})
    cfg = load_config()
    cfg["screen"]["cascade"].update(enabled=True, top_n=4, diversity_n=1, audit_n=2, final_count=final_count)
    if final_count is not None:
        cfg["screen"]["cascade"].update(diversity_n=0, audit_n=0)
    dump_resolved(cfg, tmp_path/"run_config.yaml")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = RuntimeEstimator().estimate({"kind": "analysis", "state": "running", "mock": False,
                                          "run_dir": str(tmp_path), "config_path": str(tmp_path/"run_config.yaml")}, now=100)
    assert result["status"] == "estimated"
    assert result["remaining_seconds"] == 80 + expected*30
    assert result["includes_second_stage"] is True
    assert all(p.read_bytes() == content for p, content in before.items())


def test_chai_local_msa_depth_invalidates_reuse_and_preserves_the_prior_receipt(tmp_path, monkeypatch):
    from nanocofold import screen
    cfg = load_config()
    cfg["screen"].update(msa_mode="local")
    cfg["screen"]["restraint"]["enabled"] = False
    aligned = tmp_path/"aligned"
    aligned.mkdir()
    monkeypatch.setattr(screen, "_prepare_chai_msa_directory", lambda *args: aligned)
    monkeypatch.setattr(screen.provenance, "runtime_identity", lambda *args: {"fixture": "CPU stand-in"})
    monkeypatch.setattr(screen.shell, "env_python", lambda *_: "unused-python")
    # Exercise adapter reuse and archival, not model accuracy or native inference.
    monkeypatch.setattr(screen, "_valid_chai_artifacts", lambda *args: True)
    calls = []
    def predict(env, argv, **kwargs):
        calls.append(argv)
        folder = Path(argv[argv.index("--out-dir")+1])
        folder.mkdir(parents=True, exist_ok=True)
        (folder/"standin.txt").write_text(str(len(calls)))
    monkeypatch.setattr(screen.shell, "run_in_env", predict)
    candidate = tmp_path/"candidate"
    candidate.mkdir()
    args = (cfg, candidate, {"sequence": "ACD", "target_msa": "empty"}, {"A": "ACD", "B": "ACD"}, "B", [], False)
    screen._predict_chai1(*args)
    first = (candidate/"complex_input.json").read_bytes()
    screen._predict_chai1(*args)
    assert len(calls) == 1
    cfg["screen"]["colabfold"]["msa_max_sequences"] = 4
    screen._predict_chai1(*args)
    assert len(calls) == 2
    record = json.loads((candidate/"complex_input.json").read_text())
    assert record["inputs"]["local_msa_max_sequences"] == 4
    assert any(p.read_bytes() == first for p in (candidate/".history").rglob("complex_input.json"))
