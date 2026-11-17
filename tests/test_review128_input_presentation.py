"""Researchers see portable input metadata; the server executes original settings."""
import copy
import re

import pytest
from fastapi.testclient import TestClient

from nanocofold.install import init_workspace
from nanocofold.platform.app import create_app
from nanocofold.platform.batches import BatchManager
from nanocofold.platform.inputs import InputManager
from nanocofold.platform.lab import LabAccounts


@pytest.mark.parametrize("role", ["local", "researcher", "admin"])
def test_input_paths_and_environment_use_distinct_presentation_and_execution(tmp_path, monkeypatch, role):
    init_workspace(tmp_path)
    app = create_app(tmp_path, start_workers=False)
    cfg = {"run": {"mode": "screen", "mock": True, "outdir": str(tmp_path / "runs")},
           "screen": {"engine": "boltz2", "boltz2_env": "/opt/private-engine"}}
    record = {"id": "prepared", "input_id": "prepared", "config": cfg,
              "config_path": str(tmp_path / "inputs/prepared/config.yaml")}
    monkeypatch.setattr(InputManager, "list", lambda self: [copy.deepcopy(record)])
    monkeypatch.setattr(InputManager, "get", lambda self, identifier: copy.deepcopy(record))
    monkeypatch.setattr(InputManager, "reviewed", lambda self, identifier: copy.deepcopy(record))
    monkeypatch.setattr(InputManager, "review", lambda self, payload: copy.deepcopy(record))
    seen = []
    batch_seen = []
    def review_batch(self, payload, **kwargs):
        batch_seen.append(payload)
        return {"id": "review-only"}
    monkeypatch.setattr(BatchManager, "review", review_batch)
    def create(payload, **kwargs):
        seen.append(payload)
        return {"id": "diagnostic", "state": "queued"}
    monkeypatch.setattr(app.state.jobs, "create", create)
    try:
        if role != "local":
            lab = LabAccounts(tmp_path)
            lab.set_password(role, "test-only-password")
            if role == "admin":
                lab.promote_existing_admin()
        with TestClient(app, base_url="http://127.0.0.1") as client:
            if role == "local":
                client.headers["x-platform-token"] = app.state.token
            else:
                response = client.post("/lab/login", headers={"origin": "http://127.0.0.1"},
                                       data={"name": role, "password": "test-only-password"}, follow_redirects=False)
                assert response.status_code == 303
                token = re.search(r'window.PLATFORM_TOKEN = "([0-9a-f]+)"', client.get("/").text)[1]
                client.headers["x-platform-token"] = token
            for method, url, payload in [("get", "/api/inputs", None), ("get", "/api/inputs/prepared", None),
                                         ("post", "/api/inputs/review", {})]:
                response = client.request(method, url, **({"json": payload} if payload is not None else {}))
                assert response.status_code == 200, response.text
                if role == "researcher":
                    assert str(tmp_path) not in response.text and "/opt/private-engine" not in response.text
                else:
                    assert str(tmp_path) in response.text
            assert client.get("/api/session").json()["workspace"] == ("." if role == "researcher" else str(tmp_path))
            response = client.post("/api/environment/check", json={"input_id": "prepared", "mode": "screen"})
            assert response.status_code == 200, response.text
            assert seen[-1]["config"]["screen"]["boltz2_env"] == "/opt/private-engine"
            response = client.post("/api/batches/review", json={"input_id": "prepared", "version": 1, "mode": "screen"})
            assert response.status_code == 200, response.text
            assert "input_id" not in batch_seen[-1]
            assert batch_seen[-1]["config"] == cfg
            assert record["config"] == cfg
    finally:
        app.state.inputs.close()
        app.state.jobs.close()
