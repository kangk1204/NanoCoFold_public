"""A failed batch-row save must not hide an already registered child job."""
import copy

import pytest

from nanocofold.install import init_workspace
from nanocofold.platform.batches import BatchManager
from nanocofold.platform.inputs import InputManager
from nanocofold.platform.jobs import JobManager


@pytest.fixture
def managers(tmp_path):
    init_workspace(tmp_path)
    inputs, jobs = InputManager(tmp_path), JobManager(tmp_path)
    try:
        yield BatchManager(inputs, jobs), inputs, jobs
    finally:
        jobs.close()
        inputs.close()


def manifest():
    return {"version": 1, "mode": "design", "mock": True,
            "targets_fasta": ">target\nACDEFGHIKLMNPQRSTVWY\n",
            "config": {"design": {"num_designs": 1}, "screen": {"tier2_n": 1}, "rank": {"final_n": 1}}}


def interrupted_submission(batches, monkeypatch):
    batch = batches.review(manifest())
    save = batches._save

    def fail_after_registration(value):
        if any(row.get("job_id") for row in value["rows"]):
            raise OSError("synthetic batch row persistence failure")
        return save(value)

    with monkeypatch.context() as context:
        context.setattr(batches, "_save", fail_after_registration)
        with pytest.raises(OSError, match="batch row persistence"):
            batches.submit(batch["id"], {"review_sha256": batch["review_sha256"]})
    return batch


def test_lost_row_save_recovers_existing_child_without_writing_or_creating(managers, monkeypatch):
    batches, inputs, jobs = managers
    batch = interrupted_submission(batches, monkeypatch)
    original = jobs.store.list()
    assert len(original) == 1 and original[0]["state"] == "queued"
    restarted = BatchManager(inputs, jobs)
    monkeypatch.setattr(restarted, "_save", lambda *_: pytest.fail("GET must not write batch state"))
    monkeypatch.setattr(jobs, "create", lambda *_: pytest.fail("GET must not submit work"))
    recovered = restarted.get(batch["id"])["rows"][0]
    assert recovered["job_id"] == original[0]["id"]
    assert recovered["job"]["state"] == "queued"
    assert jobs.store.list() == original


def test_cancel_after_lost_row_save_reaches_registered_child(managers, monkeypatch):
    batches, inputs, jobs = managers
    batch = interrupted_submission(batches, monkeypatch)
    child = jobs.store.list()[0]
    cancelled = BatchManager(inputs, jobs).cancel(batch["id"])
    assert jobs.store.get(child["id"])["state"] == "cancelled"
    assert cancelled["rows"][0]["job"]["state"] == "cancelled"
    assert len(jobs.store.list()) == 1


def test_explicit_retry_after_lost_save_reuses_same_child(managers, monkeypatch):
    batches, _, jobs = managers
    batch = interrupted_submission(batches, monkeypatch)
    child = jobs.store.list()[0]
    recovered = batches.submit(batch["id"], {"review_sha256": batch["review_sha256"]})
    assert recovered["rows"][0]["job_id"] == child["id"]
    assert len(jobs.store.list()) == 1


def test_registration_recovery_clears_only_stale_submission_error(managers, monkeypatch):
    batches, _, jobs = managers
    batch = interrupted_submission(batches, monkeypatch)
    # A prior failed attempt may have left an error before a later successful
    # registration lost its row save. The durable submission link is decisive.
    stored = copy.deepcopy(batches.get(batch["id"]))
    stored["rows"][0].pop("job", None)
    stored["rows"][0]["job_id"] = None
    stored["rows"][0]["submission_error"] = "old submission error"
    batches._save(stored)
    recovered = batches.get(batch["id"])["rows"][0]
    assert recovered["job_id"] == jobs.store.list()[0]["id"]
    assert recovered.get("submission_error") is None


def test_unsubmitted_batch_read_and_cancel_do_not_create_jobs(managers):
    batches, _, jobs = managers
    batch = batches.review(manifest())
    assert batches.get(batch["id"])["rows"][0]["job_id"] is None
    batches.cancel(batch["id"])
    assert jobs.store.list() == []


def test_cancel_before_start_has_no_partial_result_link_target(managers):
    batches, _, jobs = managers
    batch = batches.review(manifest())
    submitted = batches.submit(batch["id"], {"review_sha256": batch["review_sha256"]})
    child = submitted["rows"][0]["job"]
    assert child["run_dir"]  # This planned path is not evidence of saved results.
    cancelled = batches.cancel(batch["id"])["rows"][0]["job"]
    assert cancelled["state"] == "cancelled"
    assert cancelled.get("result_available") is False
    assert jobs.store.get(child["id"])["result_id"] is None


@pytest.mark.parametrize("state", ["cancelled", "interrupted", "budget_exhausted"])
def test_saved_partial_result_is_available_only_while_its_marker_exists(managers, state):
    from pathlib import Path
    batches, _, jobs = managers
    batch = batches.review(manifest())
    child = batches.submit(batch["id"], {"review_sha256": batch["review_sha256"]})["rows"][0]["job"]
    jobs.store.update(child["id"], state=state)
    root = Path(child["run_dir"])
    root.mkdir(parents=True)
    assert batches.get(batch["id"])["rows"][0]["job"].get("result_available") is False
    marker = root / "run_config.yaml"
    marker.write_text("run: {mode: design, mock: true}\n")
    assert batches.get(batch["id"])["rows"][0]["job"]["result_available"] is True
    marker.unlink()
    assert batches.get(batch["id"])["rows"][0]["job"]["result_available"] is False
