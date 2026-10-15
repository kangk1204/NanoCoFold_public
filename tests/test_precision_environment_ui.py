"""Real API/UI failure paths; no workers, GPU probing or inference are started."""
import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_environment_ui_state_and_translation_contracts():
    subprocess.run(["node", "--test", str(ROOT / "tests/precision_environment_ui_contract.mjs")],
                   check=True, capture_output=True, text=True)


@pytest.fixture
def idle_platform(tmp_path):
    import uvicorn
    from nanocofold.install import init_workspace
    from nanocofold.platform.app import create_app
    init_workspace(tmp_path)
    app = create_app(tmp_path, start_workers=False)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        yield app, f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()


def post(app, url, path, payload):
    request = Request(url + path, data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json", "X-Platform-Token": app.state.token})
    with urlopen(request) as response:
        return json.load(response)


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Real Chromium UI gate")
def test_pending_and_failed_environment_receipts_survive_reload(idle_platform):
    from playwright.sync_api import expect, sync_playwright
    app, url = idle_platform
    presets = json.loads((ROOT / "nanocofold/_data/starter-presets.json").read_text())
    payload = {"mode": "complex", "config": presets["modes"]["complex"]["native_override"]}
    job = post(app, url, "/api/environment/check", payload)
    posts = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = browser.new_page()
        page.on("request", lambda request: posts.append(request.url) if request.method == "POST" else None)
        for state in ("queued", "running", "cancelling", "failed", "interrupted", "cancelled"):
            app.state.jobs.store.update(job["id"], state=state, diagnostics=None)
            status = post(app, url, "/api/environment/status", payload)
            assert status["ready"] is False and not status["diagnostics"]
            page.goto(url)
            expect(page.locator("body")).to_have_attribute("data-startup", "ready")
            expect(page.locator('[data-view="results"]')).to_be_enabled()
            page.locator('[data-view="results"]').click()
            expect(page.locator("#view-results")).to_be_visible()
            page.locator('[data-view="environment"]').click()
            expect(page.locator("#view-environment")).to_be_visible()
            if state in {"queued", "running", "cancelling"}:
                expect(page.locator("#diagnose-environment")).to_be_disabled()
            else:
                expect(page.locator("#diagnose-environment")).to_be_enabled()
            expect(page.locator("#start-job")).to_be_disabled()
            page.locator("#language-select").select_option("ko")
            page.locator("#language-select").select_option("en")
        browser.close()
    assert not any(path.endswith(("/api/jobs", "/api/diagnostics", "/api/environment/check")) for path in posts)
    assert len(app.state.jobs.store.list()) == 1


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Real Chromium UI gate")
def test_environment_status_error_does_not_block_existing_result_navigation(idle_platform):
    from playwright.sync_api import expect, sync_playwright
    app, url = idle_platform
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = browser.new_page()
        page.route("**/api/environment/status", lambda route: route.fulfill(status=503, json={"error": "Readiness temporarily unavailable"}))
        page.goto(url)
        expect(page.locator("body")).to_have_attribute("data-startup", "ready")
        page.locator('[data-view="results"]').click()
        expect(page.locator("#view-results")).to_be_visible()
        expect(page.locator("#start-job")).to_be_disabled()
        browser.close()
    assert app.state.jobs.store.list() == []
