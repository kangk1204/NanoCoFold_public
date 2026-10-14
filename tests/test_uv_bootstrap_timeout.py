"""Real harmless process trees exercise the public UV bootstrap deadline."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bounded_uv_bootstrap", ROOT / "scripts/install_runtime.py")
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def _script(tmp_path):
    pids = tmp_path / "owned-pids"
    script = tmp_path / "harmless-installer.sh"
    script.write_text('trap "" TERM\n"$NANOCOFOLD_TEST_PYTHON" -c "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)" &\nchild=$!\nprintf "%s %s" "$$" "$child" > "$NANOCOFOLD_TEST_PIDS"\nwait "$child"\n')
    environment = {**os.environ, "NANOCOFOLD_TEST_PYTHON": sys.executable, "NANOCOFOLD_TEST_PIDS": str(pids)}
    return script, environment, pids


def _pids(path):
    deadline = time.monotonic() + 5
    while not path.is_file() and time.monotonic() < deadline:
        time.sleep(.01)
    assert path.is_file(), "Controlled shell fixture did not start"
    return [int(value) for value in path.read_text().split()]


def _assert_not_executing(pid):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "state="], text=True, capture_output=True)
        if not result.stdout.strip() or result.stdout.strip().startswith("Z"):
            return
        time.sleep(.02)
    pytest.fail("Owned installer process remained live after bounded cleanup")


@pytest.fixture
def protected_process():
    process = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], start_new_session=True)
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)


def test_timeout_cleans_actual_owned_parent_and_child_only(tmp_path, protected_process, monkeypatch):
    script, environment, pid_file = _script(tmp_path)
    original = os.killpg
    signals = []
    def signal_group(group, value):
        signals.append((group, value))
        return original(group, value)
    monkeypatch.setattr(bootstrap.os, "killpg", signal_group)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="같은 설치 명령"):
        bootstrap.run_uv_installer(script, environment, timeout_seconds=.2, grace_seconds=.05)
    parent, child = _pids(pid_file)
    assert time.monotonic() - started < 3
    assert signals == [(parent, signal.SIGTERM), (parent, signal.SIGKILL)]
    _assert_not_executing(parent)
    _assert_not_executing(child)
    assert protected_process.poll() is None
    assert protected_process.pid != parent


def test_ctrl_c_cleans_actual_owned_descendant_and_protected_process_survives(tmp_path, protected_process):
    script, environment, pid_file = _script(tmp_path)
    controller = tmp_path / "controller.py"
    receipt = tmp_path / "interrupted.json"
    controller.write_text('''import importlib.util,json,os,sys
from pathlib import Path
s=importlib.util.spec_from_file_location("bootstrap",sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
try:
    m.run_uv_installer(sys.argv[2],dict(os.environ),timeout_seconds=30,grace_seconds=.05)
except KeyboardInterrupt as error:
    Path(sys.argv[3]).write_text(json.dumps({"status":"interrupted","error":str(error)}))
    sys.exit(130)
''')
    process = subprocess.Popen([sys.executable, str(controller), str(ROOT / "scripts/install_runtime.py"), str(script), str(receipt)], env=environment, start_new_session=True)
    try:
        parent, child = _pids(pid_file)
        assert os.getpgid(parent) == parent and parent != process.pid
        os.kill(process.pid, signal.SIGINT)  # Our test controller only.
        assert process.wait(timeout=5) == 130
        result = json.loads(receipt.read_text())
        assert result["status"] == "interrupted" and "다시 시도" in result["error"]
        _assert_not_executing(parent)
        _assert_not_executing(child)
        assert protected_process.poll() is None
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_nonzero_exit_keeps_original_error_and_never_signals_reaped_group(tmp_path, monkeypatch, capfd):
    script = tmp_path / "failed.sh"
    script.write_text('echo controlled-upstream-failure >&2\nexit 17\n')
    monkeypatch.setattr(bootstrap.os, "killpg", lambda *args: pytest.fail("Completed process group must not receive a signal"))
    with pytest.raises(subprocess.CalledProcessError) as captured:
        bootstrap.run_uv_installer(script, os.environ.copy(), timeout_seconds=1, grace_seconds=.05)
    assert captured.value.returncode == 17
    assert captured.value.cmd == ["sh", str(script)]
    assert "controlled-upstream-failure" in capfd.readouterr().err


def test_reaped_process_never_receives_cleanup_signals(monkeypatch):
    process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    assert process.wait(timeout=5) == 0
    monkeypatch.setattr(bootstrap.os, "killpg", lambda *args: pytest.fail("Reaped PID must never receive group signals"))
    bootstrap.stop_owned_installer(process, .05)


def test_timeout_records_failed_attempt_without_false_install_completion(tmp_path, monkeypatch):
    script, environment, pid_file = _script(tmp_path)
    root = tmp_path / "source"
    root.mkdir()
    workspace = tmp_path / "workspace"
    old_receipt = workspace / ".platform/install-attempts/previous.json"
    old_receipt.parent.mkdir(parents=True)
    old_receipt.write_bytes(b'{"status":"failed","error":"original failure"}\n')
    before = old_receipt.read_bytes()
    monkeypatch.setattr(bootstrap, "ROOT", root)
    monkeypatch.setattr(bootstrap, "check_host", lambda *args: None)
    monkeypatch.setattr(bootstrap, "ensure_uv", lambda env: bootstrap.run_uv_installer(script, environment, timeout_seconds=.2, grace_seconds=.05))
    args = argparse.Namespace(workspace=workspace, profile="core", gpu=None, check_host=False)
    with pytest.raises(TimeoutError):
        bootstrap.install(args)
    records = [json.loads(p.read_text()) for p in old_receipt.parent.glob("*.json") if p != old_receipt]
    assert len(records) == 1 and records[0]["status"] == "failed"
    assert "uv 설치가 0.2초 제한" in records[0]["error"] and "다시 실행" in records[0]["error"]
    assert old_receipt.read_bytes() == before
    assert not (root / ".nanocofold/install-location.json").exists()
    assert not (workspace / ".platform/public-core-install.json").exists()
    for pid in _pids(pid_file):
        _assert_not_executing(pid)


def _known_uv(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\nprintf "uv ' + bootstrap.UV_VERSION + '\\n"\n')
    path.chmod(0o755)
    return path.read_bytes()


def test_exact_path_uv_returns_without_downloading_or_running_installer(tmp_path, monkeypatch):
    binary = tmp_path / "bin/uv"
    before = _known_uv(binary)
    monkeypatch.setattr(bootstrap, "run_uv_installer", lambda *a, **k: pytest.fail("Correct pinned uv must be reused"))
    assert bootstrap.ensure_uv({"PATH": str(binary.parent) + os.pathsep + "/usr/bin:/bin"}) == str(binary)
    assert binary.read_bytes() == before


def test_retry_with_exact_bootstrap_uv_reuses_binary_without_reinstallation(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    binary = tmp_path / ".nanocofold/bootstrap/bin/uv"
    before = _known_uv(binary)
    monkeypatch.setattr(bootstrap, "run_uv_installer", lambda *a, **k: pytest.fail("Successful previous binary must be reused"))
    assert bootstrap.ensure_uv({"PATH": "/usr/bin:/bin"}) == str(binary)
    assert binary.read_bytes() == before
