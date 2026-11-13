"""Password changes apply only to the signed-in account in a disposable lab."""
import json
import threading

import pytest
from fastapi.testclient import TestClient

from nanocofold.platform.app import create_app
from nanocofold.platform.lab import LabAccounts
from test_lab_candidate_permissions import sign_in


PATH = "/api/lab/password"
ORIGIN = "http://127.0.0.1"
OLD = "test-current-password"
NEW = "test-replacement-password"


def fields(current=OLD, new=NEW, **extra):
    return dict({"current": current, "new": new, "confirm": new}, **extra)


@pytest.fixture
def setup_lab(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    lab = LabAccounts(workspace)
    code = lab.initialize_admin()
    lab.set_password("Song", OLD)
    app = create_app(workspace, start_workers=False)
    yield app, lab, code
    app.state.inputs.close()


@pytest.mark.parametrize("name", ["admin", "Song"])
def test_rotate_only_own_sessions_and_keep_authorization(setup_lab, name):
    app, lab, code = setup_lab
    assert lab.activate(code, OLD)
    lab.set_shared("admin", True)
    other = "Song" if name == "admin" else "admin"
    with sign_in(app, name, OLD) as first, sign_in(app, name, OLD) as second, sign_in(app, other, OLD) as peer:
        for payload in [fields("wrong"), fields(new=OLD), fields(new="short"), fields(confirm="mismatch"),
                        fields(name=other), fields(new=None), fields(new="\ud800" * 10)]:
            # requests with invalid Unicode are deliberately supplied as JSON escape sequences
            assert first.post(PATH, content=json.dumps(payload), headers={"content-type": "application/json"}).status_code == 400
        assert first.post(PATH, json=fields(), headers={"x-platform-token": ""}).status_code == 403
        assert first.post(PATH, json=fields(), headers={"x-platform-token": peer.headers["x-platform-token"]}).status_code == 403
        assert first.post(PATH, json=fields(), headers={"origin": "https://elsewhere.invalid"}).status_code == 403
        assert first.post(PATH, data=fields()).status_code == 415
        assert first.post(PATH, content=b"x" * 16385, headers={"content-type": "application/json"}).status_code == 413
        assert first.get("/api/session").json()["researcher"] == name
        response = first.post(PATH, json=fields())
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert "Password changed." in first.get("/").text
        assert "Password changed." not in first.get("/?password=changed").text
        assert first.get("/api/session").status_code == second.get("/api/session").status_code == 401
        assert peer.get("/api/session").json()["researcher"] == other
        assert not lab.login(name, OLD)
        again = lab.login(name, NEW)
        assert lab.access(again) == (name, name == "admin", True, False)
        assert lab.login(other, OLD)


def test_no_solo_anonymous_or_unactivated_change(setup_lab, tmp_path):
    app, lab, code = setup_lab
    with TestClient(app, base_url=ORIGIN) as guest:
        assert guest.post(PATH, json=fields()).status_code == 401
    with sign_in(app, "admin", "admin") as pending:
        assert pending.post(PATH, json=fields("admin")).status_code == 403
        assert lab.activate(code, OLD)
    solo = create_app(tmp_path / "solo", start_workers=False)
    with TestClient(solo, base_url=ORIGIN) as personal:
        assert personal.post(PATH, json=fields(), headers={"x-platform-token": solo.state.token}).status_code == 403


@pytest.mark.parametrize("interrupt", ["logout", "operator_reset"])
def test_revoked_session_or_reset_wins_during_hash(setup_lab, monkeypatch, interrupt):
    _, lab, _ = setup_lab
    token = lab.login("Song", OLD)
    original_hash = lab._hash
    entered, release = threading.Event(), threading.Event()
    outcome = []

    def paused(password, salt):
        if threading.current_thread() is change and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return original_hash(password, salt)

    monkeypatch.setattr(lab, "_hash", paused)
    change = threading.Thread(target=lambda: outcome.append(lab.change_own_password(token, OLD, NEW)))
    change.start()
    try:
        assert entered.wait(10)
        if interrupt == "logout":
            lab.logout(token)
        else:
            lab.set_password("Song", "operator-reset-test", create=False)
    finally:
        release.set()
        change.join(10)
    assert outcome == [False] and not lab.login("Song", NEW)
    assert lab.login("Song", OLD if interrupt == "logout" else "operator-reset-test")
