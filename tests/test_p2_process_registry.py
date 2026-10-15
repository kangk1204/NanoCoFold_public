"""Durable command ownership, restart recovery, and stale-attempt boundaries."""
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from nanocofold.platform import jobs
from nanocofold.utils import shell


def attempt(tmp_path, monkeypatch):
    manager = jobs.JobManager(tmp_path)
    job = manager.create({"config": {"run": {"mode": "design", "mock": True},
                                     "target": {"sequence": "ACDEFGHIKLMNPQRSTVWY"}}})
    job = manager.store.update(job["id"], state="running", pid=900001,
                               process_stamp="owner", started=time.time())
    stamps = {900001: "owner", 900002: "child"}
    monkeypatch.setattr(jobs, "process_stamp", lambda pid: stamps.get(pid))
    manager.store.register_process(job["id"], 900001, "owner", 900002, "child")
    return manager, job, stamps


def test_registry_rejects_cancellation_and_stale_owner(tmp_path, monkeypatch):
    manager, job, _ = attempt(tmp_path, monkeypatch)
    manager.cancel(job["id"])
    with pytest.raises(RuntimeError, match="소유권"):
        manager.store.register_process(job["id"], 900001, "owner", 900002, "child")
    manager.store.update(job["id"], state="running", process_stamp="next-owner")
    with pytest.raises(RuntimeError, match="소유권"):
        manager.store.register_process(job["id"], 900001, "owner", 900002, "child")


def test_terminal_and_resume_wait_for_registered_child_without_observation(tmp_path, monkeypatch):
    manager, job, stamps = attempt(tmp_path, monkeypatch)
    assert not job.get("descendants")
    assert manager.store.finish(job["id"], state="complete")["state"] == "running"
    manager.store.forget_process(job["id"], 900002, "child")
    assert manager.store.registered_processes(job["id"]) == [(900002, "child")]
    manager.store.update(job["id"], state="interrupted", pid=None, process_stamp=None)
    restarted = jobs.JobManager(tmp_path)
    with pytest.raises(ValueError, match="프로세스"):
        restarted.resume(job["id"])
    stamps.pop(900002)
    assert restarted.resume(job["id"])["state"] == "queued"


def test_resume_checks_registry_inside_its_transaction(tmp_path, monkeypatch):
    manager, job, stamps = attempt(tmp_path, monkeypatch)
    manager.store.update(job["id"], state="interrupted", pid=None, process_stamp=None)
    original = manager._live_attempt_processes
    calls = []

    def raced(current, connection=None):
        calls.append(connection is not None)
        return False if connection is None else original(current, connection)

    monkeypatch.setattr(manager, "_live_attempt_processes", raced)
    with pytest.raises(ValueError, match="프로세스"):
        manager.resume(job["id"])
    assert calls == [False, True]


def test_dead_worker_stays_active_until_child_death_is_observed(tmp_path, monkeypatch):
    manager, job, stamps = attempt(tmp_path, monkeypatch)
    stamps.pop(900001)
    manager.cancel(job["id"])
    signalled = []
    monkeypatch.setattr(jobs.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(jobs.os, "killpg", lambda pid, sig: signalled.append(pid))
    manager._tick()
    assert signalled == [900002]
    assert manager.store.get(job["id"])["state"] == "cancelling"
    assert manager.store.registered_processes(job["id"])
    stamps.pop(900002)
    manager._tick()
    assert manager.store.get(job["id"])["state"] == "cancelled"
    assert not manager.store.registered_processes(job["id"])


def test_terminal_registry_only_cleanup_is_found_after_restart(tmp_path, monkeypatch):
    manager, job, stamps = attempt(tmp_path, monkeypatch)
    manager.store.update(job["id"], state="cancelled", descendants=[])
    monkeypatch.setattr(jobs.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(jobs.os, "killpg", lambda pid, sig: stamps.pop(pid, None))
    restarted = jobs.JobManager(tmp_path)
    restarted._tick()
    assert 900002 not in stamps
    assert restarted.store.registered_processes(job["id"]) == []


def test_old_cleanup_and_finish_do_not_touch_resumed_attempt(tmp_path, monkeypatch):
    manager, old, stamps = attempt(tmp_path, monkeypatch)
    stamps.pop(900002)
    manager.store.forget_process(old["id"], 900002, "child")
    stamps.update({900003: "new-owner", 900004: "new-child"})
    manager.store.update(old["id"], pid=900003, process_stamp="new-owner")
    manager.store.register_process(old["id"], 900003, "new-owner", 900004, "new-child")
    monkeypatch.setattr(jobs.os, "killpg", lambda *_: pytest.fail("must not signal new attempt"))
    assert manager._cleanup_commands(old, []) == []
    current = manager.store.finish(old["id"], state="interrupted", expected_attempt=(900001, "owner"))
    assert current["state"] == "running" and current["pid"] == 900003
    current = manager.store.update(old["id"], state="cancelling", expected_attempt=(900001, "owner"))
    assert current["state"] == "running"
    manager.pending_kills[old["id"]] = (time.monotonic() - 4, [])
    manager.pending_kill_attempts[old["id"]] = (900001, "owner")
    manager._terminate(current)
    assert old["id"] not in manager.pending_kills
    assert manager.store.get(old["id"])["state"] == "running"


@pytest.mark.parametrize("stat,expected", [("Z+", None), ("S", "Thu Oct 16 12:00:00 2026")])
def test_ps_birth_stamp_does_not_treat_zombies_as_live(monkeypatch, stat, expected):
    monkeypatch.setattr(jobs.subprocess, "run", lambda *_a, **_k:
                        SimpleNamespace(stdout=stat + " Thu Oct 16 12:00:00 2026\n"))
    assert jobs.process_stamp(99999999) == expected


@pytest.mark.parametrize("enclosing_budget", [False, True])
def test_registration_timeout_preserves_budget_classification_and_evidence(tmp_path, monkeypatch, enclosing_budget):
    manager = jobs.JobManager(tmp_path)
    job = manager.create({"config": {"run": {"mode": "design", "mock": True},
                                     "target": {"sequence": "ACDEFGHIKLMNPQRSTVWY"}}})
    job = manager.store.update(job["id"], state="running", pid=os.getpid(),
                               process_stamp=jobs.process_stamp(os.getpid()), started=time.time())
    register = manager.store.register_process

    def delayed(*args):
        register(*args)
        time.sleep(.1)

    monkeypatch.setattr(manager.store, "register_process", delayed)
    marker = tmp_path / "must-not-start"
    log = tmp_path / "command.log"
    argv = [sys.executable, "-c", "from pathlib import Path;import sys;Path(sys.argv[1]).touch()", str(marker)]
    with shell.managed_processes(manager.store, job):
        if enclosing_budget:
            with shell.execution_budget(.05), pytest.raises(shell.ExecutionBudgetExceeded) as raised:
                shell.run(argv, log_path=log, timeout=5, quiet=True)
            assert isinstance(raised.value.__cause__, subprocess.TimeoutExpired)
            assert raised.value.__cause__.output.startswith("$ ")
        else:
            with pytest.raises(subprocess.TimeoutExpired) as raised:
                shell.run(argv, log_path=log, timeout=.05, quiet=True)
            assert raised.value.output.startswith("$ ")
    assert not marker.exists()
    assert manager.store.registered_processes(job["id"]) == []
