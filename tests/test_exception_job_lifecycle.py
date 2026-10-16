"""Cancellation and interruption boundaries preserve the current durable attempt.

Every process fixture is a short-lived, owned Python sleeper. No GPU, native
engine, network request, or existing workspace is used by these regressions.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

from nanocofold.platform import jobs, worker


def _job(tmp_path, *, kind="analysis"):
    manager = jobs.JobManager(tmp_path)
    job = manager.create({"config": {"run": {"mode": "design", "mock": True},
                                     "target": {"sequence": "ACDEFGHIKLMNPQRSTVWY"}}}, kind=kind)
    return manager, job


def _registered_diagnostic(tmp_path, monkeypatch, *, state="running"):
    manager, job = _job(tmp_path, kind="diagnostics")
    manager.store.update(job["id"], state=state, pid=os.getpid(),
                         process_stamp=jobs.process_stamp(os.getpid()), started=time.time())
    # execute() ordinarily owns a separate process; this in-process fixture
    # keeps the test runner's signal handlers unchanged.
    monkeypatch.setattr(worker.signal, "signal", lambda *_: None)
    return manager, job


def test_cancel_before_worker_entry_does_not_run_diagnostics(tmp_path, monkeypatch):
    from nanocofold import diagnostics
    manager, job = _registered_diagnostic(tmp_path, monkeypatch, state="cancelling")
    called = []
    monkeypatch.setattr(diagnostics, "doctor", lambda *_a, **_k: called.append(True) or {"ok": True, "checks": []})
    assert worker.execute(tmp_path, job["id"]) == 130
    assert not called
    current = manager.store.get(job["id"])
    assert current["state"] == "cancelled" and current["exit_code"] == 130


@pytest.mark.parametrize("budget", [False, True])
def test_worker_completion_cannot_overwrite_cancel_after_its_last_read(tmp_path, monkeypatch, budget):
    from nanocofold import diagnostics
    manager, job = _registered_diagnostic(tmp_path, monkeypatch)
    returned = False
    raced = False
    original_get = jobs.JobStore.get

    def diagnosis(*_a, **_k):
        nonlocal returned
        returned = True
        return {"ok": True, "checks": []}

    def stale_get(store, identifier):
        nonlocal raced
        snapshot = original_get(store, identifier)
        if returned and not raced:
            raced = True
            manager.cancel(identifier)
            if budget:
                manager.store.update(identifier, budget_exceeded=True)
        return snapshot

    monkeypatch.setattr(diagnostics, "doctor", diagnosis)
    monkeypatch.setattr(jobs.JobStore, "get", stale_get)
    assert worker.execute(tmp_path, job["id"]) == 130
    current = original_get(manager.store, job["id"])
    assert current["state"] == ("budget_exhausted" if budget else "cancelled")
    assert current["exit_code"] == 130 and current["error"]


def test_budget_expiry_cannot_resurrect_a_completed_job_from_stale_tick(tmp_path, monkeypatch):
    manager, job = _job(tmp_path)
    manager.store.update(job["id"], state="running", phase="working", pid=9999999,
                         process_stamp="owned", started=time.time() - 5, budget_seconds=1)
    terminate = []

    def completed(_pid):
        manager.store.update(job["id"], state="complete", phase="complete", finished=123)
        return "owned"

    monkeypatch.setattr(jobs, "process_stamp", completed)
    monkeypatch.setattr(jobs, "descendants", lambda _pid: [])
    monkeypatch.setattr(manager, "_terminate", lambda current: terminate.append(current))
    manager._tick()
    current = manager.store.get(job["id"])
    assert current["state"] == "complete" and current["finished"] == 123
    assert not terminate


@pytest.mark.parametrize("state", ["cancelling", "complete"])
def test_stale_progress_cannot_replace_cancellation_or_terminal_phase(tmp_path, monkeypatch, state):
    manager, job = _job(tmp_path)
    manager.store.update(job["id"], state="running", pid=9999999, process_stamp="owned", started=time.time())

    def transitioned(_pid):
        manager.store.update(job["id"], state=state, phase=state, progress={"completed": 3, "total": 3})
        return "owned"

    monkeypatch.setattr(jobs, "process_stamp", transitioned)
    monkeypatch.setattr(jobs, "descendants", lambda _pid: [])
    monkeypatch.setattr(manager, "_progress", lambda _job: {"phase": "old_phase", "progress": {"completed": 1, "total": 3}})
    manager._tick()
    current = manager.store.get(job["id"])
    assert current["state"] == current["phase"] == state
    assert current["progress"] == {"completed": 3, "total": 3}


def test_dead_worker_reconciliation_cannot_replace_concurrent_completion(tmp_path, monkeypatch):
    manager, job = _job(tmp_path)
    manager.store.update(job["id"], state="running", pid=9999999, process_stamp="old", started=time.time())
    original_get = manager.store.get
    raced = False

    def stale_get(identifier):
        nonlocal raced
        snapshot = original_get(identifier)
        if not raced:
            raced = True
            manager.store.update(identifier, state="complete", phase="complete", finished=123)
        return snapshot

    monkeypatch.setattr(manager.store, "get", stale_get)
    monkeypatch.setattr(jobs, "process_stamp", lambda _pid: None)
    manager._tick()
    current = original_get(job["id"])
    assert current["state"] == current["phase"] == "complete"
    assert current["finished"] == 123


def test_resume_waits_for_persisted_owned_descendants_after_restart(tmp_path):
    manager, job = _job(tmp_path)
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"], start_new_session=True)
    try:
        stamp = jobs.process_stamp(child.pid)
        manager.store.update(job["id"], state="interrupted", pid=9999999, process_stamp="dead",
                             descendants=[[child.pid, stamp]])
        restarted = jobs.JobManager(tmp_path)
        with pytest.raises(ValueError, match="프로세스"):
            restarted.resume(job["id"])
        assert restarted.store.get(job["id"])["state"] == "interrupted"
    finally:
        child.kill()
        child.wait(timeout=5)


def test_resume_ignores_stale_descendant_pid_identity(tmp_path):
    manager, job = _job(tmp_path)
    manager.store.update(job["id"], state="interrupted", pid=9999999, process_stamp="dead",
                         descendants=[[os.getpid(), "not-this-process-birth"]])
    assert jobs.JobManager(tmp_path).resume(job["id"])["state"] == "queued"


def test_cancelled_descendants_remain_recoverable_if_supervisor_restarts(tmp_path):
    manager, job = _job(tmp_path)
    marker = tmp_path / "owned-child"
    source = ("import subprocess,sys,time;from pathlib import Path;"
              "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True);"
              "Path(sys.argv[1]).write_text(str(child.pid));time.sleep(60)")
    parent = subprocess.Popen([sys.executable, "-c", source, str(marker)], start_new_session=True)
    child_pid = child_stamp = None
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        child_pid = int(marker.read_text())
        child_stamp = jobs.process_stamp(child_pid)
        manager.store.update(job["id"], state="running", pid=parent.pid, process_stamp=jobs.process_stamp(parent.pid),
                             started=time.time())
        manager.cancel(job["id"])
        manager._tick()  # SIGTERM ends the parent; the independent child survives it.
        parent.wait(timeout=5)
        restarted = jobs.JobManager(tmp_path)
        restarted._tick()
        deadline = time.monotonic() + 3
        # Descendant death is asynchronous; keep the supervisor polling until
        # it has both observed the exit and persisted the terminal state.
        while time.monotonic() < deadline:
            restarted._tick()
            if (jobs.process_stamp(child_pid) is None
                    and restarted.store.get(job["id"])["state"] == "cancelled"):
                break
            time.sleep(.02)
        assert jobs.process_stamp(child_pid) is None
        assert restarted.store.get(job["id"])["state"] == "cancelled"
    finally:
        if child_pid and child_stamp and jobs.process_stamp(child_pid) == child_stamp:
            os.kill(child_pid, signal.SIGKILL)
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
