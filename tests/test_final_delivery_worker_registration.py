"""Owned CPU processes must not outlive failed worker registration."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from nanocofold.platform import jobs


def _manager(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOCOFOLD_GPU_LOCK_DIR", str(tmp_path / "locks"))
    manager = jobs.JobManager(tmp_path / "workspace")
    job = manager.create({"config": {"run": {"mode": "design", "mock": True},
                                      "target": {"sequence": "ACDEFGHIKLMNPQRSTVWY"}}})
    return manager, job


def _cpu_child(monkeypatch):
    real_popen = subprocess.Popen
    children = []

    def sleeping_child(_argv, **kwargs):
        if "nanocofold.platform.worker" not in _argv:
            return real_popen(_argv, **kwargs)
        child = real_popen([sys.executable, "-c", "import time;time.sleep(30)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(jobs.subprocess, "Popen", sleeping_child)
    return children


def _close_children(children):
    for child in children:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


@pytest.mark.parametrize("committed", [False, True], ids=["before-commit", "after-commit"])
def test_registration_write_failure_reaps_owned_child_and_keeps_lock_inode(tmp_path, monkeypatch, committed):
    manager, job = _manager(tmp_path, monkeypatch)
    children = _cpu_child(monkeypatch)
    original = manager.store.update
    fail_once = True

    def failed_registration(identifier, **updates):
        nonlocal fail_once
        if "pid" in updates and fail_once:
            fail_once = False
            if committed:
                original(identifier, **updates)
            raise OSError("fixture registration write failed")
        return original(identifier, **updates)

    monkeypatch.setattr(manager.store, "update", failed_registration)
    try:
        assert manager._spawn(job) is False
        failed = manager.store.get(job["id"])
        assert failed["state"] == "failed" and failed["can_resume"]
        assert "작업을 시작하지 못했습니다" in failed["error"]
        assert len(children) == 1 and children[0].poll() is not None
        assert job["id"] not in manager.children
        lock_path = jobs.gpu_lock_path(job["gpu"])
        identity = lock_path.stat()
        receipt = manager.store.get(job["id"])["gpu_lock"]
        assert (identity.st_dev, identity.st_ino) == (receipt["device"], receipt["inode"])
        with lock_path.open("a+") as released:
            fcntl.flock(released.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert (os.fstat(released.fileno()).st_dev, os.fstat(released.fileno()).st_ino) == (identity.st_dev, identity.st_ino)
        manager._tick()
        assert manager.store.get(job["id"])["state"] == "failed"
    finally:
        _close_children(children)
        manager.close()


def test_missing_birth_identity_rejects_launch_and_reaps_real_cpu_child(tmp_path, monkeypatch):
    manager, job = _manager(tmp_path, monkeypatch)
    children = _cpu_child(monkeypatch)
    monkeypatch.setattr(jobs, "process_stamp", lambda _pid: None)
    try:
        with pytest.raises(RuntimeError, match="등록|identity|birth"):
            manager._spawn(job)
        assert len(children) == 1 and children[0].poll() is not None
        assert job["id"] not in manager.children
    finally:
        _close_children(children)
        manager.close()


def test_launch_error_preserves_an_explicit_user_cancellation(tmp_path, monkeypatch):
    manager, job = _manager(tmp_path, monkeypatch)
    children = _cpu_child(monkeypatch)
    original = manager.store.update

    def registration_with_user_cancel(identifier, **updates):
        result = original(identifier, **updates)
        if "pid" in updates:
            manager.cancel(identifier)
            manager.store.finish(identifier, state="cancelled", can_resume=True)
            raise OSError("fixture registration write failed after user cancel")
        return result

    monkeypatch.setattr(manager.store, "update", registration_with_user_cancel)
    try:
        assert manager._spawn(job) is False
        current = manager.store.get(job["id"])
        assert current["state"] == "cancelled" and current["cancel_requested"]
        assert len(children) == 1 and children[0].poll() is not None
        assert job["id"] not in manager.children
        assert manager.resume(job["id"])["cancel_requested"] is False
    finally:
        _close_children(children)
        manager.close()


@pytest.mark.parametrize("concurrent_request", [None, "user_before_cleanup", "user_at_finish", "budget_at_finish"])
def test_after_commit_failure_reaps_real_worker_and_independent_session_child(tmp_path, monkeypatch, concurrent_request):
    manager, job = _manager(tmp_path, monkeypatch)
    marker = tmp_path / "descendant_pid"
    real_popen = subprocess.Popen
    children = []
    owned_descendants = []
    code = '''import os,subprocess,sys,time
from pathlib import Path
from nanocofold.platform import worker
from nanocofold import config
root,identifier,marker=sys.argv[1:]
def load_config(_path):
 child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True,
                        pass_fds=(int(os.environ['NANOCOFOLD_GPU_LOCK_FD']),))
 Path(marker).write_text(str(child.pid))
 time.sleep(30)
 raise RuntimeError('fixture stops before analysis')
config.load_config=load_config
raise SystemExit(worker.execute(Path(root),identifier))
'''

    def spawning_child(argv, **kwargs):
        if "nanocofold.platform.worker" not in argv:
            return real_popen(argv, **kwargs)
        environment = {**kwargs.pop("env"), "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        child = real_popen([sys.executable, "-c", code, str(manager.workspace), job["id"], str(marker)],
                           env=environment, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(jobs.subprocess, "Popen", spawning_child)
    original = manager.store.update

    def failed_registration(identifier, **updates):
        if "pid" in updates:
            original(identifier, **updates)
            deadline = time.monotonic() + 5
            while not marker.exists() and children[0].poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            assert marker.exists(), Path(job["log_path"]).read_text()
            descendant = int(marker.read_text())
            owned_descendants.append((descendant, jobs.process_stamp(descendant)))
            if concurrent_request == "user_before_cleanup":
                manager.cancel(identifier)
            raise OSError("fixture registration write failed")
        return original(identifier, **updates)

    monkeypatch.setattr(manager.store, "update", failed_registration)
    original_finish = manager.store.finish

    def finish_after_concurrent_request(identifier, **updates):
        # Deterministically place the request after _spawn's read but before
        # the terminal transaction; a stale cancellation snapshot cannot win.
        if updates.get("launch_failure"):
            if concurrent_request == "user_at_finish":
                manager.cancel(identifier)
            elif concurrent_request == "budget_at_finish":
                original(identifier, state="cancelling", budget_exceeded=True)
        return original_finish(identifier, **updates)

    monkeypatch.setattr(manager.store, "finish", finish_after_concurrent_request)
    try:
        assert manager._spawn(job) is False
        failed = manager.store.get(job["id"])
        expected = "budget_exhausted" if concurrent_request == "budget_at_finish" else "cancelled" if concurrent_request else "failed"
        assert failed["state"] == expected and failed["can_resume"]
        if concurrent_request is None:
            assert "작업을 시작하지 못했습니다" in failed["error"]
        elif concurrent_request.startswith("user"):
            assert failed["cancel_requested"] and "사용자가 작업을 취소했습니다" in failed["error"]
        else:
            assert failed["budget_exceeded"] and "실행 시간 한도" in failed["error"]
        descendant = int(marker.read_text())
        assert len(children) == 1 and children[0].poll() is not None
        assert job["id"] not in manager.children and job["id"] not in manager.failed_launches
        deadline = time.monotonic() + 3
        while jobs.process_stamp(descendant) and time.monotonic() < deadline:
            time.sleep(.02)
        assert jobs.process_stamp(descendant) is None
        lock_path = jobs.gpu_lock_path(job["gpu"])
        receipt = manager.store.get(job["id"])["gpu_lock"]
        with lock_path.open("a+") as released:
            fcntl.flock(released.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert (os.fstat(released.fileno()).st_dev, os.fstat(released.fileno()).st_ino) == (receipt["device"], receipt["inode"])
        manager._tick()
        assert manager.store.get(job["id"])["state"] == expected
    finally:
        for descendant, stamp in owned_descendants:
            if stamp and jobs.process_stamp(descendant) == stamp:
                os.kill(descendant, 9)
        _close_children(children)
        manager.close()


def test_cleanup_failure_retains_owner_and_next_tick_retries_cleanup(tmp_path, monkeypatch):
    manager, job = _manager(tmp_path, monkeypatch)
    children = _cpu_child(monkeypatch)
    original_update = manager.store.update
    original_reap = getattr(manager, "_reap_failed_launch", lambda _identifier, _child: None)
    fail_cleanup = True

    def failed_registration(identifier, **updates):
        if "pid" in updates:
            raise OSError("fixture registration write failed")
        return original_update(identifier, **updates)

    def retryable_cleanup(identifier, child):
        nonlocal fail_cleanup
        if fail_cleanup:
            fail_cleanup = False
            raise OSError("fixture cleanup temporarily unavailable")
        return original_reap(identifier, child)

    monkeypatch.setattr(manager.store, "update", failed_registration)
    monkeypatch.setattr(manager, "_reap_failed_launch", retryable_cleanup, raising=False)
    try:
        assert manager._spawn(job) is False  # Queue survives; cleanup ownership remains below.
        assert manager.children[job["id"]] is children[0]
        assert manager.failed_launches[job["id"]] is children[0]
        assert children[0].poll() is None
        manager._tick()
        assert children[0].poll() is not None
        assert job["id"] not in manager.children and job["id"] not in manager.failed_launches
        assert manager.store.get(job["id"])["state"] == "interrupted"
    finally:
        _close_children(children)
        manager.close()


@pytest.mark.parametrize("registration", ["missing", "wrong_pid", "wrong_birth", "correct"])
def test_real_worker_requires_its_own_registration_before_loading_analysis(tmp_path, monkeypatch, registration):
    manager, job = _manager(tmp_path, monkeypatch)
    manager.store.update(job["id"], state="running", started=1)
    marker = tmp_path / "analysis_loaded"
    code = '''import os,sys
from pathlib import Path
from nanocofold.platform import jobs,worker
from nanocofold import config
root,identifier,kind,marker=map(str,sys.argv[1:])
store=jobs.JobStore(Path(root))
if kind!='missing':
 store.update(identifier,pid=os.getpid()+(1 if kind=='wrong_pid' else 0),
              process_stamp='wrong' if kind=='wrong_birth' else jobs.process_stamp(os.getpid()))
# Keep the real process boundary while avoiding a five-second missing-record
# wait in this failure fixture. No analysis or external engine is run.
worker.time.sleep=lambda _seconds: None
def load_config(_path):
 Path(marker).write_text('loaded')
 raise RuntimeError('fixture stops before analysis')
config.load_config=load_config
raise SystemExit(worker.execute(Path(root),identifier))
'''
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                   "PYTHONDONTWRITEBYTECODE": "1"}
    environment.pop("NANOCOFOLD_GPU_LOCK_FD", None)
    result = subprocess.run([sys.executable, "-c", code, str(manager.workspace), job["id"], registration, str(marker)],
                            env=environment, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1, result.stdout + result.stderr
    assert marker.exists() is (registration == "correct")
    manager.close()


def test_launch_failure_cannot_finish_a_live_cleanup_worker(tmp_path, monkeypatch):
    manager, job = _manager(tmp_path, monkeypatch)
    try:
        attempt = (os.getpid(), jobs.process_stamp(os.getpid()))
        assert attempt[1]
        manager.store.update(job["id"], state="cancelling", pid=attempt[0], process_stamp=attempt[1])
        result = manager.store.finish(job["id"], state="failed", launch_failure=True, expected_attempt=attempt)
        assert result["state"] == "cancelling" and result["finished"] is None
    finally:
        manager.close()
