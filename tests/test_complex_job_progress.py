"""Actual complex model totals and independent ETA populations stay aligned."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from nanocofold.platform.eta import RuntimeEstimator
from nanocofold.platform.jobs import JobManager, JobStore

ENGINES = ["colabfold", "boltz2", "chai1", "opendde"]


def fixture_job(tmp_path, *, engines=None, completed=0, failed=0, total=None, state="running", seeds=None, models=1):
    root = tmp_path / "run"
    root.mkdir()
    settings = {"seeds": seeds or [42], "models_per_seed": models}
    settings.update({"comparison_engines": engines} if engines else {"engine": "boltz2"})
    config = root / "run_config.yaml"
    config.write_text(yaml.safe_dump({"complex": settings}))
    expected = len(settings["seeds"]) * models * (len(engines) if engines else 1)
    total = expected if total is None else total
    phases = {"input": {}, "prediction": {"completed_models": completed, "total_models": total, "failed_models": failed}}
    if state == "complete":
        phases["final"] = {"total_models": total, "successful_models": completed - failed}
    execution = {"state": state, "completed_phases": list(phases)}
    (root / "run_state.json").write_text(json.dumps({"phases": phases, "execution": execution}))
    rows = []
    engine_list = engines or ["boltz2"]
    for index in range(completed):
        engine = engine_list[min(index // (len(settings["seeds"]) * models), len(engine_list) - 1)]
        success = index >= failed
        rows.append({"engine": engine, "evaluated": success, "error": None if success else "recorded engine failure",
                     "elapsed_seconds": (index + 1) * 10 if success else None})
    (root / "complex_results.json").write_text(json.dumps({"rows": rows, "settings": settings}))
    return {"id": "fixture", "kind": "analysis", "mode": "complex", "state": state, "mock": False,
            "run_dir": str(root), "config_path": str(config), "resume": False, "created": 1., "updated": 1.,
            "progress": {"completed": 1, "total": 1}, "eta": {"completed": completed, "total": 1}}


@pytest.mark.parametrize("engines,completed,failed,state", [
    (None, 0, 0, "running"),
    (None, 1, 0, "complete"),
    (None, 1, 1, "failed"),
    (ENGINES, 0, 0, "running"),
    (ENGINES, 2, 1, "running"),
    (ENGINES, 4, 0, "complete"),
    (ENGINES, 4, 2, "complete"),
    (ENGINES, 4, 4, "failed"),
])
def test_progress_and_eta_use_same_actual_model_population(tmp_path, engines, completed, failed, state):
    job = fixture_job(tmp_path, engines=engines, completed=completed, failed=failed, state=state)
    manager = JobManager(tmp_path)
    original = {p: p.read_bytes() for p in Path(job["run_dir"]).glob("*") if p.is_file()}
    result = manager._progress(job)
    expected = len(engines) if engines else 1
    assert result["progress"] == {"completed": completed, "total": expected, "succeeded": completed - failed, "failed": failed}
    assert result["eta"]["completed"] == completed
    assert result["eta"]["total"] == expected
    assert result["eta"]["failed"] == failed
    assert {p: p.read_bytes() for p in original} == original
    if state == "complete":
        assert result["eta"]["remaining_seconds"] == 0
    if state == "failed":
        assert result["eta"]["remaining_seconds"] is None


def test_terminal_store_reads_reconcile_stale_one_of_one_without_rewriting(tmp_path):
    job = fixture_job(tmp_path, engines=ENGINES, completed=4, state="complete")
    store = JobStore(tmp_path)
    store.insert(job)
    loaded = store.get(job["id"])
    assert loaded["progress"] == {"completed": 4, "total": 4, "succeeded": 4, "failed": 0}
    assert loaded["eta"]["completed"] == loaded["eta"]["total"] == 4
    assert store.list()[0]["progress"] == loaded["progress"]
    with store.connect() as connection:
        saved = json.loads(connection.execute("SELECT payload FROM jobs WHERE id=?", (job["id"],)).fetchone()[0])
    assert saved["progress"] == {"completed": 1, "total": 1}


def test_config_plan_counts_engines_seeds_and_models_before_state_record(tmp_path):
    job = fixture_job(tmp_path, engines=["boltz2", "opendde"], seeds=[42, 43], models=3)
    path = Path(job["run_dir"]) / "run_state.json"
    path.write_text(json.dumps({"phases": {}, "execution": {"state": "running", "completed_phases": []}}))
    result = JobManager(tmp_path)._progress(job)
    assert result["progress"]["total"] == result["eta"]["total"] == 12
    assert result["eta"]["count_source"] == "configured_plan"


def test_actual_recorded_total_outranks_stale_config_plan(tmp_path):
    job = fixture_job(tmp_path, engines=ENGINES, completed=4, state="complete")
    Path(job["config_path"]).write_text("complex:\n  engine: boltz2\n  seeds: [42]\n  models_per_seed: 1\n")
    result = JobManager(tmp_path)._progress(job)
    assert result["progress"]["total"] == result["eta"]["total"] == 4
    assert result["eta"]["count_source"] == "recorded_run_state"


def test_comparison_eta_does_not_extrapolate_one_engine_to_unobserved_engines(tmp_path):
    job = fixture_job(tmp_path, engines=ENGINES, completed=2)
    result = RuntimeEstimator().estimate(job)
    assert result["completed"] == 2 and result["total"] == 4
    assert result["status"] == "awaiting_engine_samples"
    assert result["remaining_seconds"] is None
    assert result["unestimated_engines"] == ["chai1", "opendde"]


def test_comparison_eta_estimates_each_engine_from_its_own_successful_durations(tmp_path):
    job = fixture_job(tmp_path, engines=["boltz2", "opendde"], seeds=[42, 43], completed=0)
    rows = [{"engine": "boltz2", "evaluated": True, "elapsed_seconds": 10},
            {"engine": "opendde", "evaluated": True, "elapsed_seconds": 100}]
    (Path(job["run_dir"]) / "complex_results.json").write_text(json.dumps({"rows": rows}))
    result = RuntimeEstimator().estimate(job)
    assert result["status"] == "estimated"
    assert result["remaining_seconds"] == 110
    assert result["completed"] == 2 and result["total"] == 4


@pytest.mark.parametrize("corruption", ["negative", "bool", "null", "rows", "outcome"])
def test_corrupt_observations_do_not_claim_complete_counts(tmp_path, corruption):
    job = fixture_job(tmp_path, engines=ENGINES, completed=4, state="complete")
    root = Path(job["run_dir"])
    if corruption in {"negative", "bool", "null"}:
        state = json.loads((root / "run_state.json").read_text())
        state["phases"]["prediction"]["total_models"] = {"negative": -1, "bool": True, "null": None}[corruption]
        (root / "run_state.json").write_text(json.dumps(state))
    else:
        (root / "complex_results.json").write_text(json.dumps({"rows": {} if corruption == "rows" else [{}]}))
    store = JobStore(tmp_path)
    store.insert(job)
    loaded = store.get(job["id"])
    assert loaded["progress"]["total"] is None
    assert loaded["eta"]["total"] is None
    assert loaded["eta"]["remaining_seconds"] is None


def test_recorded_attempts_are_not_clamped_when_denominator_is_inconsistent(tmp_path):
    job = fixture_job(tmp_path, engines=ENGINES, completed=4, failed=2, total=1)
    result = JobManager(tmp_path)._progress(job)
    assert result["progress"] == {"completed": 4, "total": None, "succeeded": 2, "failed": 2}
    assert result["eta"]["completed"] == 4 and result["eta"]["total"] is None
    assert result["eta"]["remaining_seconds"] is None


def test_missing_observations_and_plan_remain_unknown(tmp_path):
    root = tmp_path / "missing"
    root.mkdir()
    job = {"kind": "analysis", "mode": "complex", "state": "complete", "run_dir": str(root)}
    result = RuntimeEstimator().estimate(job)
    assert result["completed"] == 0 and result["total"] is None
    assert result["remaining_seconds"] is None


def test_timing_overflow_preserves_known_model_counts(tmp_path):
    job = fixture_job(tmp_path, completed=1, seeds=[42, 43])
    (Path(job["run_dir"]) / "complex_results.json").write_text(json.dumps({"rows": [
        {"engine": "boltz2", "evaluated": True, "elapsed_seconds": 1.7976931348623157e308}]}))
    result = RuntimeEstimator().estimate(job)
    assert result["completed"] == 1 and result["total"] == 2
    assert result["remaining_seconds"] is None
    assert result["status"] == "unavailable"


@pytest.mark.parametrize("engines,expected", [(ENGINES, 4), (None, 1)])
def test_initial_complex_counter_is_full_plan_before_msa_or_prediction(tmp_path, monkeypatch, engines, expected):
    from nanocofold import complex as complex_module
    from nanocofold.config import load_config
    from nanocofold.utils.shell import ExecutionCancelled

    cfg = load_config()
    settings = {"seeds": [42], "models_per_seed": 1,
                "proteins": [{"name": "Target descriptive name", "sequence": "ACDE"},
                             {"name": "Partner descriptive name", "sequence": "FGHI"}]}
    settings.update({"comparison_engines": engines} if engines else {"engine": "boltz2"})
    cfg["complex"] = settings
    cfg["run"].update(mock=False, dry_run=False)

    class RecordedState:
        phases = None
        def __init__(self):
            self.phases = {}
        def set_phase(self, name, values):
            self.phases[name] = values

    state = RecordedState()
    def inspect_before_any_engine(*args, **kwargs):
        assert state.phases["prediction"] == {"completed_models": 0, "total_models": expected, "failed_models": 0}
        raise ExecutionCancelled("test stops before native or MSA work")

    monkeypatch.setattr("nanocofold.complex_comparison.configure", lambda config, options: config)
    monkeypatch.setattr("nanocofold.complex_comparison.prepare", inspect_before_any_engine)
    monkeypatch.setattr("nanocofold.screen._predict_boltz2", inspect_before_any_engine)
    with pytest.raises(ExecutionCancelled):
        complex_module.run(cfg, tmp_path, state)


@pytest.mark.parametrize("code,failed,expected_state", [(0, 0, "complete"), (0, 2, "partial_failure"), (1, 4, "failed")])
def test_new_worker_terminal_record_saves_actual_model_counts(tmp_path, monkeypatch, code, failed, expected_state):
    import os
    import time
    from nanocofold.platform import worker

    job = fixture_job(tmp_path, engines=ENGINES, completed=4, failed=failed)
    job.update(pid=os.getpid(), process_stamp="test-worker-stamp", started=time.time(), gpu="0")
    store = JobStore(tmp_path)
    store.insert(job)

    class Reader:
        def __init__(self, root):
            pass
        def summary(self):
            return {"failed_candidates": failed, "issues": []}

    class Relay:
        def __init__(self, *args, **kwargs):
            pass
        def start(self):
            pass
        def close(self):
            pass

    def no_reference(*args, **kwargs):
        raise FileNotFoundError("fixture has no input reference")

    monkeypatch.setattr(worker, "process_stamp", lambda pid: "test-worker-stamp")
    monkeypatch.setattr(worker.signal, "signal", lambda *args: None)
    monkeypatch.setattr(worker, "LogRelay", Relay)
    monkeypatch.setattr("nanocofold.config.load_config", lambda *args: {})
    monkeypatch.setattr("nanocofold.cli.main", lambda *args: code)
    monkeypatch.setattr("nanocofold.platform.structure_review.verified_reference", no_reference)
    monkeypatch.setattr("nanocofold.platform.results.ResultReader", Reader)
    monkeypatch.setattr("nanocofold.platform.result_catalog.ResultCatalog.register", lambda *args, **kwargs: None)
    assert worker.execute(tmp_path, job["id"]) == code
    with store.connect() as connection:
        saved = json.loads(connection.execute("SELECT payload FROM jobs WHERE id=?", (job["id"],)).fetchone()[0])
    assert saved["state"] == expected_state
    assert saved["progress"] == {"completed": 4, "total": 4, "succeeded": 4 - failed, "failed": failed}
    assert saved["eta"]["completed"] == saved["eta"]["total"] == 4
