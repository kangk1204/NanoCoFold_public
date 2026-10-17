"""No installers, servers, GPU commands or scientific engines run in these fixtures."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("recovery_bootstrap", ROOT / "scripts/install_runtime.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)
from start_server import start
from update_runtime import update
from nanocofold import cli, entrypoint
from nanocofold.platform.maintenance import maintenance_lock


@pytest.fixture
def installed(tmp_path, monkeypatch):
    repo, owner = tmp_path / "source", tmp_path / "managed workspace"
    prefix = owner / ".venv-dev"
    python = prefix / "bin/python"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"preserved managed interpreter sentinel")
    (python.parent / "nanocofold").write_bytes(b"preserved managed console sentinel")
    marker = owner / ".platform/public-core-install.json"
    bootstrap.save(marker, {"workspace": str(owner), "python": str(python), "status": "complete"})
    uv = tmp_path / "uv sentinel"
    uv.touch()
    location = repo / ".nanocofold/install-location.json"
    bootstrap.save(location, {"workspace": str(owner), "uv_path": str(uv), "gpu": None, "profile": "core"})
    result = owner / "runs/original/report.html"
    result.parent.mkdir(parents=True)
    result.write_bytes(b"preserve original research result")
    protected = {p: p.read_bytes() for p in [python, python.parent / "nanocofold", location, result]}
    monkeypatch.setattr(bootstrap, "ROOT", repo)
    monkeypatch.setattr(bootstrap, "check_host", lambda *_: None)
    monkeypatch.setattr(bootstrap, "ensure_uv", lambda *_: str(uv))
    return repo, owner, marker, protected


@pytest.mark.parametrize("interrupted", [False, True])
def test_failed_reinstall_blocks_start_and_runtime_until_explicit_retry(installed, monkeypatch, interrupted):
    repo, owner, marker, protected = installed
    def failed(argv, **kwargs):
        if interrupted:
            raise KeyboardInterrupt("owned interrupted-install fixture")
        raise subprocess.CalledProcessError(1, argv)
    monkeypatch.setattr(bootstrap.subprocess, "run", failed)
    args = argparse.Namespace(workspace=owner, profile="core", gpu=None, check_host=False)
    with pytest.raises(KeyboardInterrupt if interrupted else subprocess.CalledProcessError):
        bootstrap.install(args)
    assert json.loads(marker.read_text())["status"] == "installing"
    with pytest.raises(ValueError, match="install.sh"):
        start(repo, call=lambda *a, **kw: pytest.fail("started a partially reinstalled core"))
    with pytest.raises(ValueError, match="install.sh"):
        with maintenance_lock(owner):
            pytest.fail("runtime entered partial reinstallation")
    assert {p: p.read_bytes() for p in protected} == protected
    attempts = {p: p.read_bytes() for p in (owner / ".platform/install-attempts").glob("*.json")}
    def succeeded(argv, **kwargs):
        if "setup" in argv:
            bootstrap.save(owner / ".platform/setup-core.json", {"status": "complete", "offline_ready": True})
    monkeypatch.setattr(bootstrap.subprocess, "run", succeeded)
    bootstrap.install(args)
    assert json.loads(marker.read_text())["status"] == "complete"
    assert start(repo, call=lambda *a, **kw: 0) == 0
    assert {p: p.read_bytes() for p in attempts} == attempts
    assert {p: p.read_bytes() for p in protected if p.name != "install-location.json"} == {
        p: value for p, value in protected.items() if p.name != "install-location.json"}
    location = json.loads((repo / ".nanocofold/install-location.json").read_text())
    assert location["workspace"] == str(owner) and location["profile"] == "core" and location["gpu"] is None


def test_sigterm_of_actual_install_wrapper_keeps_live_package_child_exclusion(installed, tmp_path):
    from nanocofold.platform.jobs import process_stamp
    repo, owner, marker, protected = installed
    pid_path = tmp_path / "package.pid"
    fake_uv = tmp_path / "fake-uv"
    fake_uv.write_text("#!" + sys.executable + "\nimport os,sys\nfrom pathlib import Path\n"
                       + "Path(" + repr(str(pid_path)) + ").write_text(str(os.getpid()))\n"
                       + "sys.stdin.buffer.read(1)\n")
    fake_uv.chmod(0o700)
    code = ("import argparse,sys\nfrom pathlib import Path\n"
            + "sys.path.insert(0," + repr(str(ROOT / "scripts")) + ")\nimport install_runtime as b\n"
            + "b.ROOT=Path(" + repr(str(repo)) + ")\nb.check_host=lambda *_:None\n"
            + "b.ensure_uv=lambda *_:" + repr(str(fake_uv)) + "\n"
            + "b.install(argparse.Namespace(workspace=Path(" + repr(str(owner)) + "),profile='core',gpu=None,check_host=False))\n")
    log = (tmp_path / "wrapper.log").open("wb")
    wrapper = subprocess.Popen([sys.executable, "-I", "-c", code], stdin=subprocess.PIPE,
                               stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    pid = stamp = None
    try:
        deadline = time.monotonic() + 10
        while not pid_path.exists() and time.monotonic() < deadline:
            assert wrapper.poll() is None
            time.sleep(.02)
        assert pid_path.is_file()
        pid = int(pid_path.read_text())
        stamp = process_stamp(pid)
        assert stamp
        with pytest.raises(ValueError):
            with bootstrap.lock(owner / ".platform/public-install.lock"):
                pytest.fail("wrapper did not retain install lock")
        wrapper.terminate()  # Only our wrapper, never its group or an external job.
        wrapper.wait(timeout=5)
        assert wrapper.returncode == -15
        assert process_stamp(pid) == stamp
        with pytest.raises(ValueError):
            with bootstrap.lock(owner / ".platform/public-install.lock"):
                pytest.fail("surviving package child lost installer exclusion")
        with pytest.raises(ValueError):
            start(repo, call=lambda *a, **kw: pytest.fail("started beside live package child"))
        assert {p: p.read_bytes() for p in protected} == protected
    finally:
        if wrapper.stdin:
            wrapper.stdin.write(b"x")
            wrapper.stdin.flush()
            wrapper.stdin.close()
        if wrapper.poll() is None:
            wrapper.wait(timeout=5)
        deadline = time.monotonic() + 5
        while pid and process_stamp(pid) == stamp and time.monotonic() < deadline:
            time.sleep(.02)
        if pid:
            assert process_stamp(pid) != stamp
        log.close()
    with bootstrap.lock(owner / ".platform/public-install.lock"):
        pass
    # The child exited, but an incomplete install still requires explicit retry.
    with pytest.raises(ValueError, match="install.sh"):
        start(repo, call=lambda *a, **kw: pytest.fail("started incomplete core after child exit"))


@pytest.mark.parametrize("caller", ["entrypoint", "cli_api"])
def test_supported_cli_blocks_updater_even_when_output_is_elsewhere(installed, tmp_path, monkeypatch, caller):
    repo, owner, _, protected = installed
    monkeypatch.setattr(sys, "prefix", str(owner / ".venv-dev"))
    monkeypatch.delenv("NANOCOFOLD_INSTALL_LOCK_FD", raising=False)
    output = tmp_path / "separate source/runs/output"
    def dispatch(argv):
        assert str(output) in argv
        with pytest.raises(ValueError):
            update(repo, run=lambda *a, **kw: pytest.fail("updated active CLI interpreter"))
        return 0
    monkeypatch.setattr(cli, "_main", dispatch)
    call = entrypoint.main if caller == "entrypoint" else cli.main
    assert call(["prepare", "--mock", "--run-dir", str(output)]) == 0
    assert not (owner / ".nanocofold/jobs.sqlite3").exists()
    update(repo, run=lambda *a, **kw: None)
    assert {p: p.read_bytes() for p in protected} == protected


def test_real_python_module_entry_checks_managed_owner_before_dispatch(installed, tmp_path):
    repo, owner, _, protected = installed
    hook = tmp_path / "startup-hook"
    hook.mkdir()
    dispatched = tmp_path / "unexpected-dispatch.json"
    # Only the child interpreter's ownership and scientific dispatcher are fixtures.
    # The actual `python -m nanocofold` module and console entrypoint execute.
    (hook / "sitecustomize.py").write_text(
        "import sys,types,json\nfrom pathlib import Path\n"
        + "sys.prefix=" + repr(str(owner / ".venv-dev")) + "\n"
        + "m=types.ModuleType('nanocofold.cli')\n"
        + "m.main=lambda argv:(Path(" + repr(str(dispatched)) + ").write_text(json.dumps(argv)),0)[1]\n"
        + "sys.modules['nanocofold.cli']=m\n")
    package = Path(cli.__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(hook) + os.pathsep + str(package), "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("NANOCOFOLD_INSTALL_LOCK_FD", None)
    argv = [sys.executable, "-m", "nanocofold", "prepare", "--mock", "--run-dir", str(tmp_path / "elsewhere")]
    with bootstrap.lock(owner / ".platform/public-install.lock"):
        result = subprocess.run(argv, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=15)
        assert result.returncode == 1
        assert not dispatched.exists()
    result = subprocess.run(argv, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert dispatched.is_file()
    assert {p: p.read_bytes() for p in protected} == protected


def test_forced_cli_exit_keeps_core_reserved_until_its_real_shell_child_exits(installed, tmp_path):
    from nanocofold.platform.jobs import process_stamp
    repo, owner, _, protected = installed
    record = tmp_path / "core-child.json"
    child_code = ("import os,sys,json\nfrom pathlib import Path\n"
                  + "leases=json.loads(os.environ['NANOCOFOLD_CORE_LEASES'])\n"
                  + "Path(" + repr(str(record)) + ").write_text(json.dumps({'pid':os.getpid(),'leases':leases,'inodes':[os.fstat(fd).st_ino for fd,_ in leases]}))\n"
                  + "sys.stdin.buffer.read(1)\n")
    wrapper_code = ("import sys\nsys.prefix=" + repr(str(owner / ".venv-dev")) + "\n"
                    + "from nanocofold import cli,entrypoint\nfrom nanocofold.utils import shell\n"
                    + "def dispatch(argv):\n shell.run([sys.executable,'-I','-c'," + repr(child_code) + "],quiet=True)\n return 0\n"
                    + "cli._main=dispatch\nraise SystemExit(entrypoint.main(['prepare','--mock']))\n")
    package = Path(cli.__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(package), "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("NANOCOFOLD_INSTALL_LOCK_FD", None)
    env.pop("NANOCOFOLD_CORE_LEASES", None)
    log = (tmp_path / "cli-wrapper.log").open("wb")
    wrapper = subprocess.Popen([sys.executable, "-c", wrapper_code], env=env, cwd=tmp_path,
                               stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True)
    pid = stamp = None
    try:
        deadline = time.monotonic() + 10
        while not record.exists() and time.monotonic() < deadline:
            assert wrapper.poll() is None
            time.sleep(.02)
        assert record.is_file()
        details = json.loads(record.read_text())
        pid = details["pid"]
        stamp = process_stamp(pid)
        assert stamp and details["leases"]
        assert set(details["inodes"]) == {(owner / ".platform/public-install.lock").stat().st_ino}
        wrapper.kill()  # SIGKILL only the owned CLI wrapper, not the child's session.
        wrapper.wait(timeout=5)
        assert wrapper.returncode == -9
        assert process_stamp(pid) == stamp
        with pytest.raises(ValueError):
            update(repo, run=lambda *a, **kw: pytest.fail("updated core beside surviving shell child"))
    finally:
        if wrapper.stdin:
            wrapper.stdin.write(b"x"); wrapper.stdin.flush(); wrapper.stdin.close()
        if wrapper.poll() is None:
            wrapper.wait(timeout=5)
        deadline = time.monotonic() + 5
        while pid and process_stamp(pid) == stamp and time.monotonic() < deadline:
            time.sleep(.02)
        if pid:
            assert process_stamp(pid) != stamp
        log.close()
    update(repo, run=lambda *a, **kw: None)
    assert {p: p.read_bytes() for p in protected} == protected
