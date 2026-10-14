"""Cached history remains read only and responds to every source/lifecycle change."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path

import pytest

from nanocofold.platform.eta import MetadataReadCache, RuntimeEstimator
from nanocofold.platform.jobs import JobStore
from test_complex_job_progress import fixture_job, ENGINES


def _store(tmp_path, **kwargs):
    job = fixture_job(tmp_path, engines=ENGINES, completed=4, state="complete", **kwargs)
    store = JobStore(tmp_path); store.insert(job)
    return store, job


def test_unchanged_terminal_history_reads_no_metadata_and_keeps_sqlite_receipt(tmp_path, monkeypatch):
    store, job = _store(tmp_path)
    original = {p: p.read_bytes() for p in Path(job["run_dir"]).glob("*") if p.is_file()}
    first = store.list()[0]
    assert first["progress"]["completed"] == first["progress"]["total"] == 4
    read = Path.read_text
    def forbid_metadata(path, *args, **kwargs):
        if path in original: raise AssertionError("Unchanged terminal metadata was reread")
        return read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", forbid_metadata)
    # The API-owned copy cannot corrupt its next cached response.
    first["progress"]["completed"] = 999
    assert store.get(job["id"])["progress"]["completed"] == 4
    assert store.list()[0]["eta"]["remaining_seconds"] == 0
    with store.connect() as connection:
        saved = json.loads(connection.execute("SELECT payload FROM jobs WHERE id=?", (job["id"],)).fetchone()[0])
    assert saved["progress"] == {"completed": 1, "total": 1}
    assert {p: p.read_bytes() for p in original} == original


@pytest.mark.parametrize("change", ["rewrite", "atomic_replace_same_mtime", "same_size_same_mtime"])
def test_result_identity_changes_invalidate_cached_success(tmp_path, change):
    store, job = _store(tmp_path); store.get(job["id"])
    path = Path(job["run_dir"]) / "complex_results.json"
    old = path.stat(); payload = json.loads(path.read_text()); payload["rows"][0]["evaluated"] = False
    changed = json.dumps(payload)
    if change == "atomic_replace_same_mtime":
        replacement = path.with_suffix(".next"); replacement.write_text(changed); os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns)); replacement.replace(path)
    else:
        if change == "same_size_same_mtime":
            # false has one extra character; remove a harmless whitespace byte.
            changed = changed.replace(": ", ":", 1)
            assert len(changed.encode()) == old.st_size
        path.write_text(changed)
        if change == "same_size_same_mtime": os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    loaded = store.get(job["id"])
    assert loaded["progress"] == {"completed": 4, "total": 4, "succeeded": 3, "failed": 1}
    assert loaded["eta"]["failed"] == 1


@pytest.mark.parametrize("source", ["run_state.json", "complex_results.json", "run_config.yaml"])
def test_corrupt_source_and_restoration_are_not_hidden_by_cache(tmp_path, source):
    store, job = _store(tmp_path); store.get(job["id"])
    path = Path(job["run_dir"]) / source; original = path.read_bytes()
    if source == "run_config.yaml":
        path.write_text("complex:\n  comparison_engines: [boltz2, boltz2]\n")
    else: path.write_text("[]")
    loaded = store.get(job["id"])
    assert loaded["progress"] == {"completed": None, "total": None}
    assert loaded["eta"]["remaining_seconds"] is None
    path.write_bytes(original)
    assert store.get(job["id"])["progress"]["succeeded"] == 4


def test_deleted_sources_and_new_files_invalidate_terminal_unknown(tmp_path):
    store, job = _store(tmp_path); store.get(job["id"])
    root = Path(job["run_dir"]); original = {p: p.read_bytes() for p in root.glob("*") if p.is_file()}
    for p in original: p.unlink()
    assert store.get(job["id"])["progress"]["total"] is None
    for p, data in original.items(): p.write_bytes(data)
    assert store.get(job["id"])["progress"]["total"] == 4


def test_queue_lifecycle_change_cannot_reuse_complete_eta(tmp_path):
    store, job = _store(tmp_path); store.get(job["id"])
    store.update(job["id"], state="failed", error="preserved failure", can_resume=True)
    failed = store.get(job["id"])
    assert failed["state"] == "failed" and failed["can_resume"] is True
    assert failed["eta"]["remaining_seconds"] is None
    assert failed["progress"]["succeeded"] == 4


def test_running_estimate_recomputes_finish_time_from_unchanged_cached_metadata(tmp_path, monkeypatch):
    job = fixture_job(tmp_path, engines=None, completed=1, seeds=[42, 43], state="running")
    estimator = RuntimeEstimator(); first = estimator.estimate(job, now=100)
    read = Path.read_text
    def forbid(path, *args, **kwargs):
        if path.parent == Path(job["run_dir"]): raise AssertionError("Unchanged active metadata was reparsed")
        return read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", forbid)
    second = estimator.estimate(job, now=200)
    assert first["remaining_seconds"] == second["remaining_seconds"] == 10
    assert second["estimated_finish"] - first["estimated_finish"] == 100


def test_metadata_cache_is_bounded_and_concurrent_reads_are_consistent(tmp_path):
    cache = MetadataReadCache(limit=2)
    files = [tmp_path / str(index) for index in range(6)]
    for index, path in enumerate(files): path.write_text(json.dumps({"value": index}))
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(cache.read, files * 4))
    assert [v["value"] for v in values] == list(range(6)) * 4
    assert len(cache.entries) <= 2


def test_invalid_reuse_boolean_creates_no_job_folder(tmp_path):
    from nanocofold.platform.jobs import JobManager
    manager = JobManager(tmp_path)
    cfg = {"run": {"mock": True, "mode": "screen"}, "target": {"sequence": "ACDEFG"}}
    with pytest.raises(ValueError, match="reuse_results는 true/false"):
        manager._create({"config": cfg, "reuse_results": "false", "gpu": "0"}, identifier="invalid-reuse")
    assert not (manager.store.root / "jobs/invalid-reuse").exists()
    assert manager.store.list() == []
