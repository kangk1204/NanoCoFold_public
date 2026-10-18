"""Storage failures give safe recovery guidance without replaying mutations."""
import errno
import sqlite3

import pytest
from fastapi.testclient import TestClient

from nanocofold.platform.app import create_app


CONFIRMED_CANCEL = {
    "job_id": "unavailable", "state": "running", "pid": 9999999,
    "process_stamp": "fixture-owned-attempt", "started": 123.5,
    "resume": False, "history_length": 0,
}


@pytest.mark.parametrize("operation,confirmation", [
    ("cancel", None), ("cancel", CONFIRMED_CANCEL), ("logs", None),
], ids=["cancel", "confirmed-cancel", "logs"])
@pytest.mark.parametrize("failure,status,code,phrase", [
    (OSError(errno.ENOSPC, "private detail /srv/private/run"), 507, "storage_full", "저장 공간"),
    (PermissionError(errno.EACCES, "private detail /srv/private/run"), 503, "storage_permission", "쓰기 권한"),
    (sqlite3.OperationalError("database or disk is full"), 507, "storage_full", "저장 공간"),
    (sqlite3.OperationalError("database is locked"), 503, "storage_busy", "다른 저장 요청"),
])
def test_storage_failure_is_actionable_and_does_not_replay(tmp_path, monkeypatch, operation, confirmation, failure, status, code, phrase):
    app = create_app(tmp_path, start_workers=False)
    protected = tmp_path / "preserved-result.txt"
    protected.write_bytes(b"preserved result")
    calls = []

    def unavailable(*args, expected_cancel=None):
        # Match the real cancellation API, including its reviewed-attempt guard.
        # A signature TypeError would mask the intended storage exception.
        calls.append({"args": args, "expected_cancel": expected_cancel})
        raise failure

    monkeypatch.setattr(app.state.jobs, operation, unavailable)
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
        headers = {"x-platform-token": app.state.token}
        if operation == "cancel":
            request = {"json": {"expected_cancel": confirmation}} if confirmation is not None else {}
            response = client.post("/api/jobs/unavailable/cancel", headers=headers, **request)
        else:
            response = client.get("/api/jobs/unavailable/logs", headers=headers)
        assert response.status_code == status
        payload = response.json()
        assert payload["code"] == code
        assert phrase in payload["error"]
        assert "새로고침" in payload["error"]
        assert "private" not in response.text
        assert response.headers["cache-control"] == "no-store"
    assert len(calls) == 1
    assert calls[0]["expected_cancel"] == confirmation
    assert calls[0]["args"] == (("unavailable",) if operation == "cancel" else ("unavailable", 0))
    assert app.state.jobs.store.list() == []
    assert protected.read_bytes() == b"preserved result"


def test_missing_job_keeps_existing_not_found_contract(tmp_path):
    app = create_app(tmp_path, start_workers=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/jobs/missing/cancel", headers={"x-platform-token": app.state.token})
        assert response.status_code == 404
        assert "code" not in response.json()
