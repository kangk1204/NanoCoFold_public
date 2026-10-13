"""Computation versions identify fresh executions, never report or archive backfills."""
from __future__ import annotations

import copy
import os
import time
from pathlib import Path

import pytest

from nanocofold import __version__, cli, config
from nanocofold.state import RunState


def _prepared_run(tmp_path):
    identifier = "new-platform-job"
    root = tmp_path / "runs" / identifier
    root.mkdir(parents=True)
    cfg = config.load_config(overrides=["run.mode=screen", "run.mock=true", "target.sequence=ACDEFGHIKLMNPQRSTVWY"])
    cfg["run"]["outdir"] = str(root.parent)
    config.dump_resolved(cfg, root / "run_config.yaml")
    args = cli.build_parser().parse_args(["run", "--run-id", identifier])
    # The shared dispatcher binds new commands to a locked directory too.
    args.run_dir = str(root)
    args._existing_run_requested = False
    job = {"kind": "analysis", "id": identifier, "run_dir": str(root), "resume": False, "history": []}
    return root, cfg, args, job


def test_fresh_platform_config_can_preexist_without_losing_compute_version(tmp_path):
    root, cfg, args, job = _prepared_run(tmp_path)
    before = (root / "run_config.yaml").read_bytes()
    with cli.new_platform_computation(job):
        _, state = cli._open_run(cfg, args)
    assert state.data["application_version"] == __version__
    assert state.data["application_version_source"] == {"kind": "fresh_platform_job", "job_id": job["id"]}
    assert (root / "run_config.yaml").read_bytes() == before


def test_config_only_existing_folder_is_not_relabelled_without_a_fresh_worker(tmp_path):
    root, cfg, args, _ = _prepared_run(tmp_path)
    before = (root / "run_config.yaml").read_bytes()
    _, state = cli._open_run(cfg, args)
    assert "application_version" not in state.data and not state.path.exists()
    assert (root / "run_config.yaml").read_bytes() == before


@pytest.mark.parametrize("changes", [
    {"resume": True}, {"resume": None}, {"history": [{}]}, {"history": None},
    {"reused_result_id": "old-result"}, {"reused": True}, {"kind": "diagnostics"},
    {"id": "another-job"}])
def test_nonfresh_worker_context_cannot_backfill_an_existing_input_folder(tmp_path, changes):
    root, cfg, args, job = _prepared_run(tmp_path)
    with cli.new_platform_computation({**job, **changes}):
        _, state = cli._open_run(cfg, args)
    assert "application_version" not in state.data and not state.path.exists()


@pytest.mark.parametrize("prior_version", [None, "0.4.1"])
def test_existing_states_keep_unknown_or_original_compute_version_on_run(tmp_path, prior_version):
    root, cfg, args, job = _prepared_run(tmp_path)
    prior = RunState(root)
    if prior_version:
        prior.data["application_version"] = prior_version
    prior.set_phase("prediction", {"completed_models": 1, "total_models": 1})
    before = prior.path.read_bytes()
    with cli.new_platform_computation(job):
        _, state = cli._open_run(cfg, args)
    assert state.data.get("application_version") == prior_version
    assert prior.path.read_bytes() == before


@pytest.mark.parametrize("artifact", ["complex_results.json", "retained", "02_screen", "platform_status.json"])
def test_prior_artifacts_block_backfill_even_when_state_file_is_missing(tmp_path, artifact):
    root, cfg, args, job = _prepared_run(tmp_path)
    path = root / artifact
    if artifact in {"retained", "02_screen"}:
        path.mkdir()
    else:
        path.write_text("{}");
    with cli.new_platform_computation(job):
        _, state = cli._open_run(cfg, args)
    assert "application_version" not in state.data and not state.path.exists()


@pytest.mark.parametrize("command,new", [("report", False), ("report", True), ("score", True), ("run", True)])
def test_report_score_and_explicit_run_dir_never_backfill(tmp_path, command, new):
    root, cfg, args, job = _prepared_run(tmp_path)
    args.command = command
    args._existing_run_requested = command == "run"
    with cli.new_platform_computation(job):
        _, state = cli._open_run(cfg, args, new=new)
    assert "application_version" not in state.data and not state.path.exists()


@pytest.mark.parametrize("prior_version", [None, "0.4.1"])
def test_recovery_without_config_does_not_overwrite_a_legacy_state_version(tmp_path, prior_version):
    root, cfg, args, job = _prepared_run(tmp_path)
    (root / "run_config.yaml").unlink()
    state = RunState(root)
    if prior_version:
        state.data["application_version"] = prior_version
    state.set_phase("prediction", {"completed_models": 1})
    args._existing_run_requested = True
    with cli.new_platform_computation(job):
        _, recovered = cli._open_run(cfg, args)
    assert recovered.data.get("application_version") == prior_version


def test_worker_origin_is_scoped_to_exact_run_and_cleared_after_exit(tmp_path):
    root, cfg, args, job = _prepared_run(tmp_path)
    other = copy.deepcopy(job)
    other.update(id="other-run", run_dir=str(tmp_path / "runs/other-run"))
    with cli.new_platform_computation(other):
        _, state = cli._open_run(cfg, args)
    assert "application_version" not in state.data
    with cli.new_platform_computation(job):
        assert cli._PLATFORM_COMPUTATION_ORIGIN.get() is not None
    assert cli._PLATFORM_COMPUTATION_ORIGIN.get() is None


def test_real_new_cli_dispatch_records_version_before_handler_and_only_once(tmp_path, monkeypatch):
    root = tmp_path / "runs/new-cli"
    seen = []
    def handler(args):
        seen.append(RunState(Path(args.run_dir)).data["application_version"])
        return 0
    monkeypatch.setattr(cli, "cmd_run", handler)
    assert cli.main(["run", "--mock", "--run-id", root.name, "--set", f"run.outdir={root.parent}"]) == 0
    assert seen == [__version__]
    assert RunState(root).data["application_version_source"] == {"kind": "new_cli_computation"}


def test_registered_worker_new_preconfigured_run_records_version_through_actual_dispatch(tmp_path, monkeypatch):
    """The handler is stopped before computation; this checks worker/CLI metadata only."""
    from nanocofold.platform import jobs, worker
    manager = jobs.JobManager(tmp_path)
    try:
        job = manager.create({"config": {"run": {"mode": "design", "mock": True},
            "target": {"sequence": "ACDEFGHIKLMNPQRSTVWY"}}, "reuse_results": False})
        root = Path(job["run_dir"])
        root.mkdir(parents=True)
        (root / "run_config.yaml").write_bytes(Path(job["config_path"]).read_bytes())
        manager.store.update(job["id"], state="running", pid=os.getpid(), process_stamp="fixture-birth", started=time.time())
        monkeypatch.setattr(worker, "process_stamp", lambda pid: "fixture-birth")
        monkeypatch.setattr(worker.signal, "signal", lambda *args: None)
        seen = []
        def handler(args):
            seen.append(RunState(root).data)
            return 0
        monkeypatch.setattr(cli, "cmd_run", handler)
        assert worker.execute(tmp_path, job["id"]) == 0
        assert seen[0]["application_version"] == __version__
        assert seen[0]["application_version_source"] == {"kind": "fresh_platform_job", "job_id": job["id"]}
        assert not list(root.rglob("*.cif")) and not list(root.rglob("*.npz"))
    finally:
        manager.close()
