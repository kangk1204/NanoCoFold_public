"""Cloud resume crash recovery uses local stores/fake transport, never inference."""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from nanocofold.platform.cloud_agent import Agent
from nanocofold.platform import jobs


class Crash(BaseException):
    """Model abrupt agent loss outside its ordinary transport-error handler."""


class Cloud:
    def __init__(self):
        self.queue = {"operations": [], "jobs": []}
        self.requests = []

    def request(self, path, payload=None):
        self.requests.append((path, payload))
        return self.queue if path == "agent/queue" else {"ok": True}

    def upload(self, *_args):
        raise AssertionError("No external writes in this fixture")


@pytest.fixture
def agent(tmp_path, monkeypatch):
    cloud = Cloud()
    value = Agent(tmp_path, cloud, agent_id="resume-fixture")
    monkeypatch.setattr(value, "heartbeat", lambda: None)
    monkeypatch.setattr(value, "schedule_result", lambda *_args: None)
    try:
        yield value
    finally:
        value.manager.close()
        value.inputs.close()


def failed_job(manager):
    job = manager.create({"config": {"run": {"mode": "design", "mock": True},
                                     "target": {"sequence": "ACDEFGHIKLMNPQRSTVWY"}},
                          "budget_seconds": 20, "reuse_results": False})
    root = Path(job["run_dir"])
    root.mkdir(parents=True)
    (root / "partial.bin").write_bytes(b"preserve completed partial bytes\x00\xff")
    return manager.store.update(job["id"], state="failed", finished=time.time(), elapsed_seconds=10,
                                can_resume=True)


def pending_action(agent, job, *, action_id="resume-once", payload=None):
    payload = payload or {"kind": "resume", "budget_seconds": 60}
    with agent.manager.store.connect() as db:
        db.execute("INSERT INTO cloud_sync(id,remote_id) VALUES(?,?)", ("cloud-job", job["id"]))
    agent.client.queue["jobs"] = [{"id": "cloud-job", "agent_id": agent.agent_id,
                                    "action_id": action_id, "action": json.dumps(payload)}]


def sync_row(agent):
    with agent.manager.store.connect() as db:
        return dict(db.execute("SELECT * FROM cloud_sync WHERE id='cloud-job'").fetchone())


def terminal(manager, job, state):
    manager.store.update(job["id"], state="running", started=time.time())
    return manager.store.finish(job["id"], state=state, finished=time.time(), elapsed_seconds=13,
                                can_resume=state != "complete")


@pytest.mark.parametrize("state", ["failed", "cancelled", "budget_exhausted", "partial_failure", "interrupted", "complete"])
def test_agent_crash_after_resume_commit_never_replays_same_action(agent, monkeypatch, state):
    job = failed_job(agent.manager)
    pending_action(agent, job)
    config = Path(job["config_path"]).read_bytes()
    sentinel = Path(job["run_dir"]) / "partial.bin"
    original = sentinel.read_bytes()
    resume = agent.manager.resume

    def crash_after_commit(*args, **kwargs):
        result = resume(*args, **kwargs)
        assert result["state"] == "queued"
        raise Crash("after resume commit, before cloud_sync acknowledgement")

    with monkeypatch.context() as patch:
        patch.setattr(agent.manager, "resume", crash_after_commit)
        with pytest.raises(Crash):
            agent.tick()
    assert sync_row(agent)["action_id"] is None
    assert len(agent.manager.store.get(job["id"])["history"]) == 1
    terminal(agent.manager, job, state)
    # A new JobManager reconstructs every receipt from the existing database.
    restarted = jobs.JobManager(agent.workspace)
    monkeypatch.setattr(agent, "manager", restarted)
    agent.tick()
    current = restarted.store.get(job["id"])
    assert current["state"] == state
    assert len(current["history"]) == 1
    assert current["budget_seconds"] == 70
    assert sync_row(agent)["action_id"] == "resume-once"
    agent.tick()
    assert len(restarted.store.get(job["id"])["history"]) == 1
    assert sentinel.read_bytes() == original
    assert Path(job["config_path"]).read_bytes() == config


@pytest.mark.parametrize("after_insert", [False, True])
def test_crash_during_receipt_transaction_rolls_back_both_mutations(agent, monkeypatch, after_insert):
    job = failed_job(agent.manager)
    pending_action(agent, job)
    connect = agent.manager.store.connect

    class Connection:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, *args):
            receipt = sql.startswith("INSERT INTO job_resume_actions")
            if receipt and not after_insert:
                raise Crash("before receipt insert")
            result = self.db.execute(sql, *args)
            if receipt:
                raise Crash("after receipt insert, before transaction commit")
            return result

        def __getattr__(self, key):
            return getattr(self.db, key)

    @contextlib.contextmanager
    def interrupted_connection():
        with connect() as db:
            yield Connection(db)

    with monkeypatch.context() as patch:
        patch.setattr(agent.manager.store, "connect", interrupted_connection)
        with pytest.raises(Crash):
            agent.tick()
    current = agent.manager.store.get(job["id"])
    assert current["state"] == "failed" and current["history"] == []
    assert current["budget_seconds"] == 20
    assert sync_row(agent)["action_id"] is None
    with connect() as db:
        assert db.execute("SELECT COUNT(*) FROM job_resume_actions").fetchone()[0] == 0
    agent.tick()
    current = agent.manager.store.get(job["id"])
    assert current["state"] == "queued" and len(current["history"]) == 1
    assert current["budget_seconds"] == 70


@pytest.mark.parametrize("change", ["budget", "other_field", "job", "kind"])
def test_same_action_id_with_changed_payload_or_job_is_rejected(agent, change):
    job = failed_job(agent.manager)
    payload = {"kind": "resume", "budget_seconds": 60}
    agent.manager.resume(job["id"], 60, action_id="bound-action", action_payload=payload)
    terminal(agent.manager, job, "failed")
    target = failed_job(agent.manager) if change == "job" else job
    changed = dict(payload)
    if change == "budget":
        changed["budget_seconds"] = 61
    elif change == "other_field":
        changed["extra"] = "changed request"
    elif change == "kind":
        changed["kind"] = "cancel"
    before = agent.manager.store.get(target["id"])
    with pytest.raises(ValueError, match="action|payload|요청"):
        agent.manager.resume(target["id"], changed.get("budget_seconds"),
                             action_id="bound-action", action_payload=changed)
    assert agent.manager.store.get(target["id"]) == before


@pytest.mark.parametrize("payload", [{"kind": "resume", "budget_seconds": 61}, {"kind": "cancel"}])
def test_cloud_validates_conflict_even_after_action_acknowledgement(agent, payload):
    job = failed_job(agent.manager)
    pending_action(agent, job)
    agent.tick()
    terminal(agent.manager, job, "failed")
    agent.client.queue["jobs"][0]["action"] = json.dumps(payload)
    agent.tick()
    assert "cloud-job" in agent.sync_errors
    assert "action" in agent.sync_errors["cloud-job"]
    assert len(agent.manager.store.get(job["id"])["history"]) == 1
    assert agent.manager.store.get(job["id"])["state"] == "failed"


@pytest.mark.parametrize("action_id", ["", "  ", True, 7, {}, "a" * 201])
def test_malformed_receipt_identity_never_mutates_job(agent, action_id):
    job = failed_job(agent.manager)
    with pytest.raises(ValueError, match="action"):
        agent.manager.resume(job["id"], 60, action_id=action_id)
    assert agent.manager.store.get(job["id"])["history"] == []


@pytest.mark.parametrize("budget", [float("nan"), float("inf"), -float("inf"), True, 0, -1, "60", 604801])
def test_receipt_cannot_admit_invalid_additional_budget(agent, budget):
    job = failed_job(agent.manager)
    with pytest.raises(ValueError):
        agent.manager.resume(job["id"], budget, action_id="invalid-budget")
    assert agent.manager.store.get(job["id"])["history"] == []


def test_rejected_action_is_not_consumed_and_can_be_corrected(agent):
    job = failed_job(agent.manager)
    agent.manager.store.update(job["id"], state="budget_exhausted")
    with pytest.raises(ValueError):
        agent.manager.resume(job["id"], action_id="not-consumed")
    resumed = agent.manager.resume(job["id"], 60, action_id="not-consumed")
    assert len(resumed["history"]) == 1 and resumed["budget_seconds"] == 70


def test_receipt_budget_must_match_the_actual_resume_budget(agent):
    job = failed_job(agent.manager)
    with pytest.raises(ValueError, match="payload|budget|요청"):
        agent.manager.resume(job["id"], 60, action_id="mismatched-budget",
                             action_payload={"kind": "resume", "budget_seconds": 600})
    assert agent.manager.store.get(job["id"])["history"] == []


def test_replayed_receipt_does_not_accept_boolean_budget_as_one_second(agent):
    job = failed_job(agent.manager)
    payload = {"kind": "resume", "budget_seconds": 1}
    agent.manager.resume(job["id"], 1, action_id="finite-budget", action_payload=payload)
    with pytest.raises(ValueError):
        agent.manager.resume(job["id"], True, action_id="finite-budget", action_payload=payload)
    assert len(agent.manager.store.get(job["id"])["history"]) == 1


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), {"non_json"}, object()])
def test_receipt_rejects_nonfinite_or_nonjson_extra_payload(agent, invalid):
    job = failed_job(agent.manager)
    with pytest.raises(ValueError, match="payload"):
        agent.manager.resume(job["id"], 60, action_id="invalid-extra",
                             action_payload={"kind": "resume", "budget_seconds": 60, "extra": invalid})
    assert agent.manager.store.get(job["id"])["history"] == []


def test_applied_receipt_is_read_only_even_when_attempt_is_live_or_snapshot_changed(agent):
    job = failed_job(agent.manager)
    agent.manager.resume(job["id"], 60, action_id="already-applied")
    live = agent.manager.store.update(job["id"], state="running", pid=os.getpid(),
                                     process_stamp=jobs.process_stamp(os.getpid()), started=time.time())
    Path(job["config_path"]).write_text("synthetic snapshot changed after applied action\n")
    replayed = agent.manager.resume(job["id"], 60, action_id="already-applied")
    assert replayed == live
    assert len(replayed["history"]) == 1


def test_concurrent_duplicate_action_is_applied_once_and_retries_succeed(agent):
    job = failed_job(agent.manager)
    managers = [jobs.JobManager(agent.workspace), jobs.JobManager(agent.workspace)]
    barrier = threading.Barrier(2)

    def resume(manager):
        barrier.wait(timeout=5)
        try:
            return manager.resume(job["id"], 60, action_id="concurrent-action")
        except ValueError as exc:
            # The existing nonblocking catalog lock can ask one caller to retry.
            assert "복구 중" in str(exc)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(resume, managers))
    assert any(result is not None for result in results)
    for manager in managers:
        replayed = manager.resume(job["id"], 60, action_id="concurrent-action")
        assert len(replayed["history"]) == 1 and replayed["budget_seconds"] == 70
        manager.close()


def test_concurrent_same_action_for_different_jobs_has_one_winner(agent):
    selected = [failed_job(agent.manager), failed_job(agent.manager)]
    managers = [jobs.JobManager(agent.workspace), jobs.JobManager(agent.workspace)]
    barrier = threading.Barrier(2)

    def resume(index):
        barrier.wait(timeout=5)
        try:
            return managers[index].resume(selected[index]["id"], 60, action_id="one-global-action")
        except ValueError as exc:
            assert "different job or payload" in str(exc)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(resume, range(2)))
    assert sum(result is not None for result in results) == 1
    assert sorted(len(agent.manager.store.get(job["id"])["history"]) for job in selected) == [0, 1]
    with agent.manager.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM job_resume_actions").fetchone()[0] == 1
    for manager in managers:
        manager.close()


def test_legacy_direct_resume_remains_explicit_and_unreceipted(agent):
    job = failed_job(agent.manager)
    first = agent.manager.resume(job["id"], 60)
    assert len(first["history"]) == 1
    terminal(agent.manager, job, "failed")
    second = agent.manager.resume(job["id"], 60)
    assert len(second["history"]) == 2 and second["budget_seconds"] == 73
    with agent.manager.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM job_resume_actions").fetchone()[0] == 0


def test_legacy_acknowledged_action_without_receipt_is_not_reapplied(agent):
    job = failed_job(agent.manager)
    pending_action(agent, job)
    with agent.manager.store.connect() as db:
        db.execute("UPDATE cloud_sync SET action_id='resume-once'")
    agent.tick()
    assert agent.manager.store.get(job["id"])["history"] == []


def test_cloud_resume_without_stable_action_id_fails_closed(agent):
    job = failed_job(agent.manager)
    pending_action(agent, job, action_id=None)
    agent.tick()
    assert "cloud-job" in agent.sync_errors
    assert agent.manager.store.get(job["id"])["history"] == []


def test_agent_advertises_atomic_resume_receipt_capability():
    assert Agent.capabilities()["resume_action_receipts_v1"] is True
