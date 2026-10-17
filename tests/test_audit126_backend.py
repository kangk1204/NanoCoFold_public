"""Bounded polling, batch checkpoints and exclusive local startup; no inference."""
import inspect
import socket

import pytest
from fastapi.testclient import TestClient

from nanocofold.install import init_workspace
from nanocofold.platform import app as app_module
from nanocofold.platform.batches import BatchManager
from nanocofold.platform.inputs import InputManager
from nanocofold.platform.jobs import JobManager


def test_history_and_active_streams_are_bounded_and_page_without_skips(tmp_path):
    app = app_module.create_app(tmp_path, start_workers=False)
    for number in range(150):
        app.state.jobs.store.insert({"id": f"job-{number:03d}", "kind": "diagnostics", "state": "queued",
                                     "created": float(number), "updated": float(number)})
    # Newer terminal history must not hide the bounded active stream.
    for number in range(150, 157):
        app.state.jobs.store.insert({"id": f"job-{number:03d}", "kind": "diagnostics", "state": "complete",
                                     "created": float(number), "updated": float(number)})
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.headers["X-Platform-Token"] = app.state.token
        first = client.get("/api/jobs?limit=5").json()
        assert len(first["jobs"]) == len(first["active_jobs"]) == 5
        assert first["jobs"][0]["id"] == "job-156"
        assert first["active_jobs"][0]["id"] == "job-149"
        for active, expected in ((False, 157), (True, 150)):
            seen, cursor = [], {}
            while True:
                response = client.get("/api/jobs", params={"limit": 5, "active": str(active).lower(), **cursor})
                assert response.status_code == 200
                page = response.json()
                assert len(page["jobs"]) <= 5 and len(page.get("active_jobs", [])) <= 5
                seen.extend(row["id"] for row in page["jobs"])
                if page["next_cursor"] is None:
                    break
                cursor = page["next_cursor"]
            assert len(seen) == len(set(seen)) == expected


@pytest.mark.parametrize("profile", ["not-a-profile", None, [], {}, True])
def test_invalid_setup_never_creates_orphan_job_files(tmp_path, profile):
    app = app_module.create_app(tmp_path, start_workers=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.headers["X-Platform-Token"] = app.state.token
        before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
        assert client.post("/api/setup", json={"profile": profile}).status_code == 400
        assert app.state.jobs.store.list() == []
        after = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
        assert after == before


def test_batch_checkpoints_are_constant_and_replay_reuses_all_children(tmp_path, monkeypatch):
    init_workspace(tmp_path)
    inputs, jobs = InputManager(tmp_path), JobManager(tmp_path)
    try:
        batches = BatchManager(inputs, jobs)
        batch = batches.review({"version": 1, "mode": "design", "mock": True,
                                "targets_fasta": "".join(f">target-{i}\nACDEFGHIKLMNPQRSTVWY\n" for i in range(8))})
        checkpoints, save = [], batches._save
        def counted(value):
            checkpoints.append(len([r for r in value["rows"] if r.get("job_id")]))
            return save(value)
        monkeypatch.setattr(batches, "_save", counted)
        submitted = batches.submit(batch["id"], {"review_sha256": batch["review_sha256"]})
        ids = [r["job_id"] for r in submitted["rows"]]
        assert checkpoints == [0, 8] and len(set(ids)) == 8
        retry = batches.submit(batch["id"], {"review_sha256": batch["review_sha256"]})
        assert [r["job_id"] for r in retry["rows"]] == ids
        assert len(jobs.store.list()) == 8
    finally:
        jobs.close()
        inputs.close()


def test_occupied_port_does_not_open_other_app_or_initialize_workspace(tmp_path, monkeypatch):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        monkeypatch.setattr(app_module, "create_app", lambda *_: pytest.fail("Do not initialize a workspace before owning the port"))
        monkeypatch.setattr(app_module.webbrowser, "open", lambda *_: pytest.fail("Do not open an unrelated application"))
        with pytest.raises(ValueError, match="already in use.*--port 0"):
            app_module.serve(tmp_path, port=port)
        assert listener.getsockname()[1] == port
        assert not list(tmp_path.iterdir())


def test_platform_default_is_distinct_and_owned_socket_closes(tmp_path, monkeypatch, capsys):
    import uvicorn
    assert inspect.signature(app_module.serve).parameters["port"].default == 18765
    sockets = []
    class Server:
        def __init__(self, config):
            pass
        def run(self, *, sockets):
            globals_socket = sockets[0]
            assert globals_socket.getsockname()[1] > 0
            observed.append(globals_socket)
    observed = sockets
    monkeypatch.setattr(app_module, "create_app", lambda *_: object())
    monkeypatch.setattr(uvicorn, "Server", Server)
    app_module.serve(tmp_path, port=0, no_browser=True)
    text = capsys.readouterr().out
    assert "NanoCoFold: http://127.0.0.1:" in text
    assert "ExitOnForwardFailure=yes" in text and sockets[0].fileno() == -1
