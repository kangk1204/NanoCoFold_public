"""Confirmed cancellation is bound atomically to the reviewed attempt.

No worker, prediction engine, GPU, network service or existing workspace runs.
"""
import copy
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nanocofold.platform.jobs import JobManager, cancellation_snapshot


def create(manager, name="test"):
    return manager.create({"name": name, "mock": True, "reuse_results": False,
                           "config": {"run": {"mode": "design", "mock": True},
                                      "target": {"sequence": "ACDEFGHIKLMNPQRSTVWY"}}})


@pytest.fixture
def manager(tmp_path):
    value = JobManager(tmp_path)
    yield value
    value.close()


def test_confirmed_queued_cancel_keeps_saved_outputs_and_resumes_same_job(manager):
    job = create(manager)
    output = Path(job["run_dir"])
    output.mkdir(parents=True)
    sentinel = output / "original-result.txt"
    sentinel.write_bytes(b"saved original result")
    result = manager.cancel(job["id"], expected_cancel=cancellation_snapshot(job))
    assert result["state"] == "cancelled"
    assert sentinel.read_bytes() == b"saved original result"
    resumed = manager.resume(job["id"])
    assert resumed["id"] == job["id"] and resumed["state"] == "queued"
    assert sentinel.read_bytes() == b"saved original result"
    with pytest.raises(ValueError, match="상태가 바뀌었습니다"):
        manager.cancel(job["id"], expected_cancel=cancellation_snapshot(job))
    assert manager.store.get(job["id"])["state"] == "queued"


@pytest.mark.parametrize("changes", [
    {"state": "running", "started": 42.5, "pid": 9999999, "process_stamp": "new"},
    {"started": 43.5}, {"pid": 9999999}, {"process_stamp": "new"},
    {"resume": True}, {"history": [{"state": "interrupted", "finished": 40}]},
    {"state": "complete", "finished": 44},
])
def test_state_or_generation_changed_after_read_never_cancels_new_attempt(manager, changes):
    job = create(manager)
    expected = cancellation_snapshot(job)
    latest = manager.store.update(job["id"], **changes)
    with pytest.raises(ValueError, match="상태가 바뀌었습니다"):
        manager.cancel(job["id"], expected_cancel=expected)
    assert manager.store.get(job["id"]) == latest


@pytest.mark.parametrize("change", [
    lambda v: v.update(history_length=True),
    lambda v: v.update(resume=0),
    lambda v: v.update(pid=False),
    lambda v: v.update(started=False),
    lambda v: v.update(extra="ignored"),
    lambda v: v.pop("job_id"),
    lambda v: v.update(job_id="another-job"),
])
def test_malformed_confirmation_never_changes_state(manager, change):
    job = create(manager)
    expected = cancellation_snapshot(job)
    change(expected)
    with pytest.raises(ValueError, match="상태가 바뀌었습니다"):
        manager.cancel(job["id"], expected_cancel=expected)
    assert manager.store.get(job["id"]) == job


def test_live_progress_is_not_part_of_confirmation_identity(manager):
    job = create(manager)
    job = manager.store.update(job["id"], state="running", pid=9999999,
                               process_stamp="owned", started=time.time())
    expected = cancellation_snapshot(job)
    manager.store.update(job["id"], phase="predicting", progress={"completed": 2, "total": 10})
    result = manager.cancel(job["id"], expected_cancel=expected)
    assert result["state"] == "cancelling" and result["progress"]["completed"] == 2


def test_batch_confirmation_is_all_or_nothing_on_one_new_attempt(manager):
    first, second = create(manager, "one"), create(manager, "two")
    expected = [cancellation_snapshot(first), cancellation_snapshot(second)]
    newer = manager.store.update(second["id"], resume=True, history=[{"state": "interrupted"}])
    with pytest.raises(ValueError, match="상태가 바뀌었습니다"):
        manager.cancel_many(expected)
    assert manager.store.get(first["id"]) == first
    assert manager.store.get(second["id"]) == newer
    expected = [cancellation_snapshot(first), cancellation_snapshot(newer)]
    results = manager.cancel_many(expected)
    assert [r["state"] for r in results] == ["cancelled", "cancelled"]


def test_duplicate_batch_targets_cannot_issue_any_cancellation(manager):
    job = create(manager)
    expected = cancellation_snapshot(job)
    with pytest.raises(ValueError):
        manager.cancel_many([expected, copy.deepcopy(expected)])
    assert manager.store.get(job["id"])["state"] == "queued"


def test_batch_manager_rejects_missing_new_member_before_cancelling_any(tmp_path):
    from nanocofold.install import init_workspace
    from nanocofold.platform.inputs import InputManager
    from nanocofold.platform.batches import BatchManager
    init_workspace(tmp_path)
    inputs, jobs = InputManager(tmp_path), JobManager(tmp_path)
    try:
        batches = BatchManager(inputs, jobs)
        batch = batches.review({"version": 1, "mode": "design", "mock": True,
                                "targets_fasta": ">one\nACDEFGHIKLMNPQRSTVWY\n>two\nACDEFGHIKLMNPQRSTVWY\n",
                                "config": {"design": {"num_designs": 1}}})
        batch = batches.submit(batch["id"], {"review_sha256": batch["review_sha256"]})
        original = jobs.store.list()
        expected = {"batch_id": batch["id"], "jobs": [cancellation_snapshot(batch["rows"][0]["job"])]}
        with pytest.raises(ValueError, match="상태가 바뀌었습니다"):
            batches.cancel(batch["id"], expected_cancel=expected)
        assert jobs.store.list() == original
        expected["jobs"] = [cancellation_snapshot(row["job"]) for row in batch["rows"]]
        assert all(row["job"]["state"] == "cancelled"
                   for row in batches.cancel(batch["id"], expected_cancel=expected)["rows"])
    finally:
        inputs.close()
        jobs.close()


def test_batch_member_resumed_between_precheck_and_write_is_atomic(tmp_path, monkeypatch):
    from nanocofold.install import init_workspace
    from nanocofold.platform.inputs import InputManager
    from nanocofold.platform.batches import BatchManager
    init_workspace(tmp_path)
    inputs, jobs = InputManager(tmp_path), JobManager(tmp_path)
    try:
        batches = BatchManager(inputs, jobs)
        batch = batches.review({"version": 1, "mode": "design", "mock": True,
                                "targets_fasta": ">one\nACDEFGHIKLMNPQRSTVWY\n>two\nACDEFGHIKLMNPQRSTVWY\n",
                                "config": {"design": {"num_designs": 1}}})
        batch = batches.submit(batch["id"], {"review_sha256": batch["review_sha256"]})
        first, second = [row["job"] for row in batch["rows"]]
        first, second = jobs.store.get(first["id"]), jobs.store.get(second["id"])
        jobs.cancel(second["id"])
        expected = {"batch_id": batch["id"], "jobs": [cancellation_snapshot(first)]}
        original = jobs.cancel_many
        resumed = []
        def race(guards, **kwargs):
            resumed.append(jobs.resume(second["id"]))
            return original(guards, **kwargs)
        monkeypatch.setattr(jobs, "cancel_many", race)
        with pytest.raises(ValueError, match="상태가 바뀌었습니다"):
            batches.cancel(batch["id"], expected_cancel=expected)
        assert jobs.store.get(first["id"]) == first
        assert jobs.store.get(second["id"]) == resumed[0]
    finally:
        inputs.close()
        jobs.close()


def test_api_requires_current_confirmation_but_preserves_legacy_empty_body(tmp_path):
    from nanocofold.install import init_workspace
    from nanocofold.platform.app import create_app
    init_workspace(tmp_path)
    app = create_app(tmp_path, start_workers=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = {"x-platform-token": app.state.token}
        job = create(app.state.jobs)
        expected = cancellation_snapshot(job)
        app.state.jobs.store.update(job["id"], resume=True, history=[{"state": "cancelled"}])
        response = client.post("/api/jobs/" + job["id"] + "/cancel",
                               json={"expected_cancel": expected}, headers=headers)
        assert response.status_code == 400
        assert app.state.jobs.store.get(job["id"])["state"] == "queued"
        current = app.state.jobs.store.get(job["id"])
        response = client.post("/api/jobs/" + job["id"] + "/cancel",
                               json={"expected_cancel": cancellation_snapshot(current)}, headers=headers)
        assert response.status_code == 200 and response.json()["state"] == "cancelled"
        legacy = create(app.state.jobs, "legacy")
        response = client.post("/api/jobs/" + legacy["id"] + "/cancel", headers=headers)
        assert response.status_code == 200 and response.json()["state"] == "cancelled"
