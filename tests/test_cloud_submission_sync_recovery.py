"""Durable cloud submissions survive synchronization failures without inference."""
from __future__ import annotations

import contextlib
import json
import sqlite3

import pytest

from nanocofold.platform.cloud_agent import Agent
from nanocofold.platform.jobs import JobManager


class Crash(BaseException):
    """Abrupt synchronizer loss after the local submission is committed."""


class Cloud:
    def __init__(self):
        self.queue = {"operations": [], "jobs": []}
        self.requests = []
        self.status_timeout = False

    def request(self, path, payload=None):
        self.requests.append((path, payload))
        if path == "agent/queue":
            return self.queue
        if path == "agent/status" and self.status_timeout:
            self.status_timeout = False
            raise TimeoutError("status acknowledgement timed out")
        return {"ok": True}


@pytest.fixture
def agent(tmp_path, monkeypatch):
    value = Agent(tmp_path, Cloud(), agent_id="submission-fixture")
    monkeypatch.setattr(value, "heartbeat", lambda: None)
    # The manager is never started: these tests cannot launch a worker or GPU.
    monkeypatch.setattr(value, "schedule_result", lambda *_args: pytest.fail("no result exists"))
    monkeypatch.setattr(value, "reviewed_diagnostics_config", lambda *_args: (
        {"run": {"mode": "design", "mock": True},
         "target": {"sequence": "ACDEFGHIKLMNPQRSTVWY"}}, {}))
    value.client.queue["jobs"] = [{
        "id": "cloud-submission-1234", "agent_id": value.agent_id,
        "request": json.dumps({"input_id": "reviewed-input", "mode": "design",
                               "name": "submission fixture", "budget_seconds": 20}),
    }]
    try:
        yield value
    finally:
        value.manager.close()
        value.inputs.close()


def statuses(agent):
    return [payload["status"] for path, payload in agent.client.requests if path == "agent/status"]


def mapping(agent):
    with agent.manager.store.connect() as db:
        row = db.execute("SELECT * FROM cloud_sync WHERE id='cloud-submission-1234'").fetchone()
        return dict(row) if row else None


def interrupt_mapping(agent, monkeypatch, error):
    connect = agent.manager.store.connect
    failures = []

    class Connection:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, *args):
            if sql.startswith("INSERT INTO cloud_sync") and not failures:
                failures.append(True)
                raise error
            return self.db.execute(sql, *args)

        def __getattr__(self, key):
            return getattr(self.db, key)

    @contextlib.contextmanager
    def interrupted():
        with connect() as db:
            yield Connection(db)

    monkeypatch.setattr(agent.manager.store, "connect", interrupted)


@pytest.mark.parametrize("state", ["queued", "running", "complete"])
def test_first_status_timeout_preserves_created_job_and_retries_mapping(agent, monkeypatch, state):
    created = []
    create = agent.manager._create

    def counted_create(*args, **kwargs):
        job = create(*args, **kwargs)
        if state != "queued":
            job = agent.manager.store.update(job["id"], state=state)
        created.append(job["id"])
        return job

    monkeypatch.setattr(agent.manager, "_create", counted_create)
    agent.client.status_timeout = True
    agent.tick()
    assert len(created) == 1 and agent.manager.store.count() == 1
    pending = mapping(agent)
    assert pending["remote_id"] == created[0] and pending["result_synced"] == 0
    assert all(status["state"] != "failed" for status in statuses(agent))
    assert "TimeoutError" in agent.sync_errors["cloud-submission-1234"]
    before = agent.manager.store.get(created[0])
    # The durable mapping also retries if a subsequent Site queue omits the job.
    agent.client.queue["jobs"] = []
    agent.tick()
    assert len(created) == 1 and agent.manager.store.get(created[0]) == before
    assert mapping(agent) == pending
    assert statuses(agent)[-1]["id"] == created[0]
    assert statuses(agent)[-1]["state"] == state
    assert not agent.sync_errors


def test_log_read_failure_after_submission_is_only_a_sync_error(agent, monkeypatch):
    logs = agent.manager.logs
    monkeypatch.setattr(agent.manager, "logs", lambda *_args: (_ for _ in ()).throw(OSError("log unavailable")))
    agent.tick()
    assert agent.manager.store.count() == 1 and mapping(agent)["result_synced"] == 0
    assert statuses(agent) == []
    assert "OSError" in agent.sync_errors["cloud-submission-1234"]
    monkeypatch.setattr(agent.manager, "logs", logs)
    agent.tick()
    assert agent.manager.store.count() == 1 and statuses(agent)[-1]["state"] == "queued"
    assert not agent.sync_errors


def test_mapping_write_failure_never_marks_durable_job_failed(agent, monkeypatch):
    interrupt_mapping(agent, monkeypatch, sqlite3.OperationalError("mapping temporarily unavailable"))
    agent.tick()
    assert agent.manager.store.count() == 1 and mapping(agent) is None
    assert statuses(agent) == []
    assert "OperationalError" in agent.sync_errors["cloud-submission-1234"]
    job = agent.manager.store.list()[0]
    agent.tick()
    assert agent.manager.store.count() == 1 and mapping(agent)["remote_id"] == job["id"]
    assert agent.manager.store.get(job["id"]) == job


def test_crash_before_mapping_recovers_committed_snapshot_without_revalidation(agent, monkeypatch):
    interrupt_mapping(agent, monkeypatch, Crash("after create commit, before cloud mapping"))
    with pytest.raises(Crash):
        agent.tick()
    job = agent.manager.store.list()[0]
    assert mapping(agent) is None
    agent.manager.close()
    monkeypatch.setattr(agent, "manager", JobManager(agent.workspace))
    # Like an already mapped submission, the durable independent snapshot is
    # authoritative even if a reviewed-input receipt changes after restart.
    monkeypatch.setattr(agent, "reviewed_diagnostics_config", lambda *_args: pytest.fail("committed job revalidated"))
    agent.tick()
    assert agent.manager.store.count() == 1 and mapping(agent)["remote_id"] == job["id"]
    assert agent.manager.store.get(job["id"]) == job
    assert statuses(agent)[-1]["state"] == "queued"


def test_error_after_create_commit_is_retried_without_false_failure(agent, monkeypatch):
    create = agent.manager.create

    def committed_then_error(payload):
        create(payload)
        raise OSError("submission acknowledgement interrupted")

    monkeypatch.setattr(agent.manager, "create", committed_then_error)
    agent.tick()
    assert agent.manager.store.count() == 1 and mapping(agent) is None
    assert statuses(agent) == []
    assert "OSError" in agent.sync_errors["cloud-submission-1234"]
    monkeypatch.setattr(agent.manager, "create", create)
    agent.tick()
    assert agent.manager.store.count() == 1 and mapping(agent) is not None
    assert statuses(agent)[-1]["state"] == "queued"


def test_actual_create_validation_failure_is_still_reported_failed(agent):
    request = json.loads(agent.client.queue["jobs"][0]["request"])
    request["budget_seconds"] = 0
    agent.client.queue["jobs"][0]["request"] = json.dumps(request)
    agent.tick()
    assert agent.manager.store.count() == 0 and mapping(agent) is None
    assert len(statuses(agent)) == 1
    assert statuses(agent)[0]["id"] is None and statuses(agent)[0]["state"] == "failed"
    assert "7" in statuses(agent)[0]["error"]

