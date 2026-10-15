import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from update_runtime import update
from install_runtime import _process_stamp, lock
from start_server import start
from nanocofold.platform.maintenance import maintenance_lock


@pytest.fixture
def installed_workspace(tmp_path):
    repo = tmp_path / "repo"
    workspace = tmp_path / "workspace"
    python = workspace / ".venv-dev/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    (python.parent / "nanocofold").touch()
    uv = tmp_path / "uv"
    uv.touch()
    location = repo / ".nanocofold/install-location.json"
    location.parent.mkdir(parents=True)
    location.write_text(json.dumps({"workspace": str(workspace), "uv_path": str(uv),
                                    "gpu": "GPU-existing", "profile": "complex"}))
    marker = workspace / ".platform/public-core-install.json"
    marker.parent.mkdir()
    marker.write_text(json.dumps({"workspace": str(workspace), "python": str(python), "status": "complete"}))
    result = workspace / "runs/original/report.html"
    result.parent.mkdir(parents=True)
    result.write_text("preserve existing result")
    protected = {path: path.read_bytes() for path in (location, marker, result)}
    return repo, workspace, protected


def assert_preserved(protected):
    assert {path: path.read_bytes() for path in protected} == protected


@pytest.fixture
def waiting_child():
    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"], stdin=subprocess.PIPE)
    try:
        yield child
    finally:
        if child.poll() is None:
            try:
                child.communicate(b"x", timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate()


@pytest.mark.parametrize("source", ["worker", "descendant"])
def test_terminal_job_still_finishing_blocks_update(installed_workspace, waiting_child, source):
    from nanocofold.platform.jobs import JobManager, process_stamp

    repo, workspace, protected = installed_workspace
    manager = JobManager(workspace)
    try:
        stamp = process_stamp(waiting_child.pid)
        assert stamp and _process_stamp(waiting_child.pid) == stamp
        job = manager.create({"mode": "complex", "gpu": "0"}, kind="diagnostics")
        identity = {"pid": waiting_child.pid, "process_stamp": stamp} if source == "worker" else {
            "descendants": [[waiting_child.pid, stamp]]}
        manager.store.update(job["id"], state="complete", **identity)
        before = manager.store.get(job["id"])
        with pytest.raises(ValueError, match="still finishing"):
            update(repo, run=lambda *a, **k: pytest.fail("updated a terminal worker still writing results"))
        assert waiting_child.poll() is None
        assert manager.store.get(job["id"]) == before
        waiting_child.communicate(b"x", timeout=5)
        update(repo, run=lambda *a, **k: None)
        assert_preserved(protected)
    finally:
        manager.close()


def test_stale_terminal_pid_does_not_block_or_touch_unrelated_live_process(installed_workspace, waiting_child):
    from nanocofold.platform.jobs import JobManager, process_stamp

    repo, workspace, protected = installed_workspace
    manager = JobManager(workspace)
    try:
        stamp = process_stamp(waiting_child.pid)
        assert stamp
        job = manager.create({"mode": "complex", "gpu": "0"}, kind="diagnostics")
        manager.store.update(job["id"], state="complete", pid=waiting_child.pid,
                             process_stamp="different-birth-identity:" + stamp)
        update(repo, run=lambda *a, **k: None)
        assert waiting_child.poll() is None
        assert_preserved(protected)
    finally:
        manager.close()


def test_update_excludes_already_running_old_supervisor(installed_workspace):
    repo, workspace, protected = installed_workspace
    with lock(workspace / ".nanocofold/supervisor.lock"):
        with pytest.raises(ValueError):
            update(repo, run=lambda *a, **k: pytest.fail("changed core while old scheduler was active"))
    assert not (workspace / ".platform/core-update-state.json").exists()
    assert_preserved(protected)


def test_runtime_and_start_hold_shared_lock_until_they_exit(installed_workspace):
    repo, workspace, protected = installed_workspace
    with maintenance_lock(workspace), maintenance_lock(workspace):
        with pytest.raises(ValueError):
            update(repo, run=lambda *a, **k: pytest.fail("updated active runtime"))

    def child(args, **kwargs):
        assert args[-2:] == ["--port", "8766"]
        assert kwargs["env"]["NANOCOFOLD_GPU"] == "GPU-existing"
        assert len(kwargs["pass_fds"]) == 1
        assert os.fstat(kwargs["pass_fds"][0]).st_ino == (workspace / ".platform/public-install.lock").stat().st_ino
        with pytest.raises(ValueError):
            update(repo, run=lambda *a, **k: pytest.fail("updated during server child"))
        return 0

    assert start(repo, ["--port", "8766"], call=child) == 0
    update(repo, run=lambda *a, **k: None)
    assert_preserved(protected)


def test_live_app_and_updater_exclude_each_other(installed_workspace):
    from fastapi.testclient import TestClient
    from nanocofold.platform.app import create_app

    repo, workspace, protected = installed_workspace
    app = create_app(workspace, start_workers=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/").status_code == 200
        with pytest.raises(ValueError):
            update(repo, run=lambda *a, **k: pytest.fail("updated live app"))
    with lock(workspace / ".platform/public-install.lock"):
        blocked = create_app(workspace, start_workers=False)
        with pytest.raises(ValueError):
            with TestClient(blocked, base_url="http://127.0.0.1"):
                pytest.fail("started app during update")
    update(repo, run=lambda *a, **k: None)
    with TestClient(create_app(workspace, start_workers=False), base_url="http://127.0.0.1") as client:
        assert client.get("/").status_code == 200
    assert_preserved(protected)


@pytest.mark.parametrize("state", ["queued", "running", "cancelling"])
def test_update_refuses_detached_work_after_server_shutdown(installed_workspace, state):
    from nanocofold.platform.jobs import JobManager

    repo, workspace, protected = installed_workspace
    manager = JobManager(workspace)
    try:
        job = manager.create({"mode": "complex", "gpu": "0"}, kind="diagnostics")
        manager.store.update(job["id"], state=state)
        manager.close()
        with pytest.raises(ValueError, match="작업"):
            update(repo, run=lambda *a, **k: pytest.fail("updated detached work"))
        assert manager.store.get(job["id"])["state"] == state
        assert_preserved(protected)
    finally:
        manager.close()


def test_update_blocks_new_submissions_resume_and_start_then_releases(installed_workspace):
    from nanocofold.platform.jobs import JobManager

    repo, workspace, protected = installed_workspace
    manager = JobManager(workspace)
    payload = {"mode": "complex", "mock": True, "gpu": "0", "config": {
        "run": {"mode": "complex", "mock": True}, "target": {"sequence": "ACDEFG"},
        "complex": {"proteins": [{"sequence": "ACDEFG"}, {"sequence": "KLMNPQ"}]}}}
    try:
        previous = manager.create(payload)
        manager.store.update(previous["id"], state="interrupted")
        count = manager.store.count()
        calls = []

        def package_step(*args, **kwargs):
            calls.append(args)
            assert len(kwargs["pass_fds"]) == 2
            assert {os.fstat(fd).st_ino for fd in kwargs["pass_fds"]} == {
                (workspace / ".platform/public-install.lock").stat().st_ino,
                (workspace / ".nanocofold/supervisor.lock").stat().st_ino,
            }
            with pytest.raises(ValueError):
                manager.create({"mode": "complex", "gpu": "0"}, kind="diagnostics")
            with pytest.raises(ValueError):
                manager.resume(previous["id"])
            with pytest.raises(ValueError):
                start(repo, call=lambda *a, **k: pytest.fail("started during update"))
            assert manager.store.count() == count
            assert manager.store.get(previous["id"])["state"] == "interrupted"

        update(repo, run=package_step)
        assert len(calls) == 4
        assert manager.resume(previous["id"])["state"] == "queued"
        assert_preserved(protected)
    finally:
        manager.close()


def test_interrupted_update_retains_lock_until_its_package_child_exits(installed_workspace):
    repo, workspace, protected = installed_workspace
    children = []

    def interrupted_command(args, **kwargs):
        # A harmless Python child stands in for a surviving package command.
        children.append(subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
            stdin=subprocess.PIPE, pass_fds=kwargs["pass_fds"], env=kwargs["env"]))
        raise KeyboardInterrupt()

    try:
        with pytest.raises(KeyboardInterrupt):
            update(repo, run=interrupted_command)
        assert children[0].poll() is None
        with pytest.raises(ValueError):
            update(repo, run=lambda *a, **k: pytest.fail("raced a surviving package child"))
    finally:
        for child in children:
            try:
                child.communicate(b"x", timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate()
    update(repo, run=lambda *a, **k: None)
    assert_preserved(protected)


@pytest.mark.parametrize("step", [1, 2, 3, 4])
@pytest.mark.parametrize("interrupted", [False, True])
def test_failed_or_interrupted_update_requires_retry_before_start(installed_workspace, step, interrupted):
    repo, workspace, protected = installed_workspace
    calls = []

    def fail(args, **kwargs):
        calls.append(args)
        if len(calls) == step:
            if interrupted:
                raise KeyboardInterrupt()
            raise subprocess.CalledProcessError(1, args)

    with pytest.raises(KeyboardInterrupt if interrupted else subprocess.CalledProcessError):
        update(repo, run=fail)
    current = workspace / ".platform/core-update-state.json"
    state = json.loads(current.read_text())
    assert state["status"] == "failed"
    archive = Path(state["receipt_path"])
    failed_bytes = archive.read_bytes()
    with pytest.raises(ValueError, match="bash update.sh"):
        start(repo, call=lambda *a, **k: pytest.fail("started partially updated core"))
    with pytest.raises(ValueError, match="bash update.sh"):
        with maintenance_lock(workspace):
            pytest.fail("used partially updated core")
    assert_preserved(protected)

    update(repo, run=lambda *a, **k: None)
    assert json.loads(current.read_text())["status"] == "complete"
    assert archive.read_bytes() == failed_bytes
    with maintenance_lock(workspace):
        pass
    assert start(repo, call=lambda *a, **k: 0) == 0
    assert_preserved(protected)


@pytest.mark.parametrize("state", ['{"status":"updating"}', "null", "[]", "{truncated"])
def test_start_and_runtime_fail_closed_on_unfinished_or_corrupt_state(installed_workspace, state):
    repo, workspace, protected = installed_workspace
    (workspace / ".platform/core-update-state.json").write_text(state)
    with pytest.raises(ValueError, match="bash update.sh"):
        start(repo, call=lambda *a, **k: pytest.fail("started unverified core"))
    with pytest.raises(ValueError, match="bash update.sh"):
        with maintenance_lock(workspace):
            pytest.fail("used unverified core")
    update(repo, run=lambda *a, **k: None)
    assert start(repo, call=lambda *a, **k: 0) == 0
    assert_preserved(protected)


def test_update_preserves_gpu_location_and_results_and_invokes_no_inference(tmp_path):
    repo=tmp_path/"repo"; workspace=tmp_path/"workspace"
    python=workspace/".venv-dev/bin/python";python.parent.mkdir(parents=True);python.touch()
    uv=tmp_path/"uv";uv.touch()
    location=repo/".nanocofold/install-location.json";location.parent.mkdir(parents=True)
    location.write_text(json.dumps({"workspace":str(workspace),"uv_path":str(uv),"gpu":"GPU-existing","profile":"complex"}))
    marker=workspace/".platform/public-core-install.json";marker.parent.mkdir()
    marker.write_text(json.dumps({"workspace":str(workspace),"python":str(python),"status":"complete"}))
    result=workspace/"runs/original/report.html";result.parent.mkdir(parents=True);result.write_text("preserve me")
    before=(location.read_bytes(),marker.read_bytes(),result.read_bytes());calls=[]
    update(repo,run=lambda args,**kwargs:calls.append((args,kwargs)))
    assert (location.read_bytes(),marker.read_bytes(),result.read_bytes())==before
    assert len(calls)==4
    for args,kwargs in calls:
        assert "setup" not in args and "doctor" not in args and "run" not in args
        assert kwargs["env"]["CUDA_VISIBLE_DEVICES"]==""
    assert list((workspace/".platform/core-updates").glob("*.json"))
    import sqlite3
    db=workspace/".nanocofold/jobs.sqlite3";db.parent.mkdir(exist_ok=True)
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE jobs (state TEXT)")
        connection.execute("INSERT INTO jobs VALUES ('running')")
    calls.clear()
    with pytest.raises(ValueError,match="작업"):
        update(repo,run=lambda args,**kwargs:calls.append((args,kwargs)))
    assert not calls and (location.read_bytes(),marker.read_bytes(),result.read_bytes())==before


def test_update_refuses_unmanaged_environment_without_installing(tmp_path):
    location=tmp_path/".nanocofold/install-location.json";location.parent.mkdir()
    location.write_text(json.dumps({"workspace":str(tmp_path),"uv_path":"/missing"}))
    with pytest.raises(OSError):
        update(tmp_path,run=lambda *a,**k:pytest.fail("unmanaged install changed"))
