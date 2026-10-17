"""Managed interpreter ownership is separate from source and output roots."""
import json
import os
from pathlib import Path
import sys

import pytest

from nanocofold import cli, entrypoint
from nanocofold.platform.maintenance import maintenance_lock


@pytest.fixture
def owned_core(tmp_path, monkeypatch):
    owner = tmp_path / "installed workspace"
    prefix = owner / ".venv-dev"
    (prefix / "bin").mkdir(parents=True)
    marker = owner / ".platform/public-core-install.json"
    marker.parent.mkdir()
    record = {"workspace": str(owner), "python": str(prefix / "bin/python"), "status": "complete"}
    marker.write_text(json.dumps(record))
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.delenv("NANOCOFOLD_INSTALL_LOCK_FD", raising=False)
    return owner, marker, record


def exclusive(path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


@pytest.mark.parametrize("state", ["installing", "failed", "interrupted", None])
def test_incomplete_reinstallation_blocks_workspace_and_owned_cli(owned_core, monkeypatch, state):
    owner, marker, record = owned_core
    record["status"] = state
    marker.write_text(json.dumps(record))
    before = marker.read_bytes()
    with pytest.raises(ValueError, match="install.sh"):
        with maintenance_lock(owner):
            pytest.fail("entered incomplete managed installation")
    monkeypatch.setattr(cli, "_main", lambda *_: pytest.fail("CLI entered incomplete managed core"))
    assert cli.main(["prepare", "--mock"]) == 1
    assert marker.read_bytes() == before


@pytest.mark.parametrize("change", [{"workspace": "/another-workspace"}, {"python": "/another-python"}, None])
def test_invalid_managed_marker_never_admits_cli(owned_core, monkeypatch, change):
    _, marker, record = owned_core
    marker.write_text("{truncated" if change is None else json.dumps({**record, **change}))
    monkeypatch.setattr(cli, "_main", lambda *_: pytest.fail("invalid managed marker admitted"))
    assert cli.main(["prepare", "--mock"]) == 1


@pytest.mark.parametrize("entry", ["console", "cli_api"])
def test_owned_cli_excludes_core_update_when_output_is_elsewhere(owned_core, tmp_path, monkeypatch, entry):
    import fcntl
    owner, _, _ = owned_core
    output = tmp_path / "another output root/run"
    commands = []
    def command(argv):
        commands.append(argv)
        assert str(output) in argv
        path = owner / ".platform/public-install.lock"
        with path.open("a+") as contender:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return 0
    monkeypatch.setattr(cli, "_main", command)
    caller = entrypoint.main if entry == "console" else cli.main
    assert caller(["prepare", "--mock", "--run-dir", str(output)]) == 0
    assert len(commands) == 1
    with exclusive(owner / ".platform/public-install.lock"):
        pass  # The execution released its shared reservation.
    assert not (owner / ".nanocofold/jobs.sqlite3").exists()


@pytest.mark.parametrize("entry", ["console", "cli_api"])
def test_active_core_update_blocks_supported_cli_before_dispatch(owned_core, monkeypatch, entry):
    owner, _, _ = owned_core
    monkeypatch.setattr(cli, "_main", lambda *_: pytest.fail("dispatched during package update"))
    caller = entrypoint.main if entry == "console" else cli.main
    with exclusive(owner / ".platform/public-install.lock"):
        assert caller(["prepare", "--mock"]) == 1


@pytest.mark.parametrize("command", ["init", "setup"])
def test_installer_handoff_preserves_exclusive_ownership(owned_core, monkeypatch, command):
    import fcntl
    owner, marker, record = owned_core
    marker.write_text(json.dumps({**record, "status": "installing"}))
    with exclusive(owner / ".platform/public-install.lock") as owned:
        monkeypatch.setenv("NANOCOFOLD_INSTALL_LOCK_FD", str(owned.fileno()))
        def dispatch(_):
            with (owner / ".platform/public-install.lock").open("a+") as contender:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(contender.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            return 0
        monkeypatch.setattr(cli, "_main", dispatch)
        assert cli.main([command]) == 0
        # Handoff must neither downgrade nor unlock the parent's reservation.
        with (owner / ".platform/public-install.lock").open("a+") as contender:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)


@pytest.mark.parametrize("command", ["prepare", "run", "serve", "report"])
def test_install_handoff_cannot_admit_other_commands(owned_core, monkeypatch, command):
    owner, marker, record = owned_core
    marker.write_text(json.dumps({**record, "status": "installing"}))
    with exclusive(owner / ".platform/public-install.lock") as owned:
        monkeypatch.setenv("NANOCOFOLD_INSTALL_LOCK_FD", str(owned.fileno()))
        monkeypatch.setattr(cli, "_main", lambda *_: pytest.fail("installation bypass admitted analysis"))
        assert cli.main([command]) == 1


@pytest.mark.parametrize("invalid", ["closed", "other_inode", "shared", "unlocked", "not_integer"])
def test_invalid_or_nonexclusive_install_handoff_is_rejected(owned_core, tmp_path, monkeypatch, invalid):
    import fcntl
    owner, marker, record = owned_core
    marker.write_text(json.dumps({**record, "status": "installing"}))
    path = owner / ".platform/public-install.lock"
    path.touch()
    handle = None
    if invalid == "not_integer":
        value = "invalid"
    elif invalid == "closed":
        fd = os.open(path, os.O_RDWR)
        os.close(fd)
        value = str(fd)
    else:
        handle = (tmp_path / "other.lock" if invalid == "other_inode" else path).open("a+")
        if invalid != "unlocked":
            fcntl.flock(handle.fileno(), (fcntl.LOCK_SH if invalid == "shared" else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        value = str(handle.fileno())
    try:
        monkeypatch.setenv("NANOCOFOLD_INSTALL_LOCK_FD", value)
        monkeypatch.setattr(cli, "_main", lambda *_: pytest.fail("invalid handoff admitted setup"))
        assert cli.main(["setup"]) == 1
    finally:
        if handle is not None:
            handle.close()


def test_existing_unmanaged_source_environment_remains_usable(tmp_path, monkeypatch):
    owner = tmp_path / "unmanaged source"
    prefix = owner / ".venv-dev"
    prefix.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(cli, "_main", lambda *_: 17)
    monkeypatch.delenv("NANOCOFOLD_INSTALL_LOCK_FD", raising=False)
    assert cli.main(["prepare", "--mock"]) == 17
    assert not (owner / ".platform").exists()


def test_nested_source_and_core_shared_leases_are_compatible(owned_core, monkeypatch):
    owner, _, _ = owned_core
    monkeypatch.setattr(cli, "_main", lambda *_: 0)
    with maintenance_lock(owner), maintenance_lock(owner):
        assert cli.main(["prepare", "--mock"]) == 0


@pytest.mark.parametrize("mode", ["shared", "exclusive"])
def test_child_lease_handoff_never_changes_parent_lock_mode(owned_core, monkeypatch, mode):
    import fcntl
    from nanocofold.platform.maintenance import CORE_LEASE_ENV, core_lease_handoffs
    owner, _, _ = owned_core
    path = owner / ".platform/public-install.lock"
    with path.open("a+") as held:
        fcntl.flock(held.fileno(), (fcntl.LOCK_SH if mode == "shared" else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        monkeypatch.setenv(CORE_LEASE_ENV, json.dumps([[held.fileno(), str(owner)]]))
        assert core_lease_handoffs() == ((held.fileno(), str(owner)),)
        with path.open("a+") as contender:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if mode == "exclusive":
                with pytest.raises(BlockingIOError):
                    fcntl.flock(contender.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)


@pytest.mark.parametrize("invalid", ["closed", "other_inode", "unlocked", "invalid_json", "bad_shape"])
def test_invalid_child_lease_metadata_never_launches_command(owned_core, tmp_path, monkeypatch, invalid):
    from nanocofold.platform.maintenance import CORE_LEASE_ENV, core_lease_handoffs
    owner, _, _ = owned_core
    path = owner / ".platform/public-install.lock"
    path.touch()
    handle = None
    if invalid in {"invalid_json", "bad_shape"}:
        encoded = "{truncated" if invalid == "invalid_json" else json.dumps([[True, str(owner)]])
    elif invalid == "closed":
        descriptor = os.open(path, os.O_RDONLY)
        os.close(descriptor)
        encoded = json.dumps([[descriptor, str(owner)]])
    else:
        handle = exclusive(tmp_path / "other-file" if invalid == "other_inode" else path)
        if invalid == "unlocked":
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        encoded = json.dumps([[handle.fileno(), str(owner)]])
    try:
        monkeypatch.setenv(CORE_LEASE_ENV, encoded)
        with pytest.raises(ValueError, match="lease"):
            core_lease_handoffs()
    finally:
        if handle is not None:
            handle.close()


def test_concurrent_contexts_keep_their_own_lease_without_global_environment_mutation(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from nanocofold.platform.maintenance import CORE_LEASE_ENV, _bind_core_lease, core_lease_handoffs
    monkeypatch.delenv(CORE_LEASE_ENV, raising=False)
    barrier = threading.Barrier(2)
    def run(number):
        owner = tmp_path / str(number)
        marker = owner / ".platform/public-core-install.json"
        marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps({"workspace": str(owner), "python": str(owner / ".venv-dev/bin/python"), "status": "complete"}))
        with maintenance_lock(owner) as descriptor, _bind_core_lease(descriptor, owner):
            barrier.wait(timeout=5)
            assert core_lease_handoffs() == ((descriptor, str(owner)),)
            assert CORE_LEASE_ENV not in os.environ
        assert core_lease_handoffs() == ()
        return number
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, [1, 2])) == [1, 2]
    assert core_lease_handoffs() == () and CORE_LEASE_ENV not in os.environ


def test_inherited_descriptor_does_not_borrow_a_different_readers_reservation(owned_core, monkeypatch):
    import fcntl
    from nanocofold.platform.maintenance import CORE_LEASE_ENV, core_lease_handoffs
    owner, _, _ = owned_core
    path = owner / ".platform/public-install.lock"
    with path.open("a+") as transferred:
        with path.open("a+") as unrelated_reader:
            fcntl.flock(unrelated_reader.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            monkeypatch.setenv(CORE_LEASE_ENV, json.dumps([[transferred.fileno(), str(owner)]]))
            assert core_lease_handoffs() == ((transferred.fileno(), str(owner)),)
        # The unrelated reader exited. The descriptor actually transferred to a
        # child must now hold its own reservation, not a stale borrowed claim.
        with path.open("a+") as writer:
            with pytest.raises(BlockingIOError):
                fcntl.flock(writer.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_run_in_env_and_process_guard_preserve_gpu_and_core_reservations(owned_core, tmp_path, monkeypatch):
    import fcntl
    from nanocofold.platform.jobs import JobManager, process_stamp
    from nanocofold.platform.maintenance import CORE_LEASE_ENV, installed_core_lease
    from nanocofold.utils import shell
    owner, _, _ = owned_core
    manager = JobManager(tmp_path / "isolated-jobstore")
    job = manager.create({"config": {"run": {"mode": "design", "mock": True},
                                     "target": {"sequence": "ACDEFGHIKLMNPQRSTVWY"}}})
    job = manager.store.update(job["id"], state="running", pid=os.getpid(),
                               process_stamp=process_stamp(os.getpid()))
    source = ("import os,json;leases=json.loads(os.environ['NANOCOFOLD_CORE_LEASES']);"
              "print('PAYLOAD:'+json.dumps({'leases':leases,'core_inodes':[os.fstat(fd).st_ino for fd,_ in leases],"
              "'gpu_inode':os.fstat(int(os.environ['NANOCOFOLD_GPU_LOCK_FD'])).st_ino,"
              "'sentinel':os.environ['LEASE_TEST_SENTINEL']}))")
    log = tmp_path / "guarded-command.log"
    try:
        with exclusive(tmp_path / "ordinary-gpu-reservation.lock") as gpu:
            monkeypatch.setenv("NANOCOFOLD_GPU_LOCK_FD", str(gpu.fileno()))
            monkeypatch.delenv(CORE_LEASE_ENV, raising=False)
            with installed_core_lease("prepare"), shell.managed_processes(manager.store, job):
                result = shell.run_in_env(None, [sys.executable, "-I", "-c", source],
                                          extra_env={CORE_LEASE_ENV: None, "LEASE_TEST_SENTINEL": "preserved"},
                                          quiet=True, log_path=log)
                assert result.returncode == 0
                payload = json.loads(next(line.removeprefix("PAYLOAD:") for line in log.read_text().splitlines()
                                          if line.startswith("PAYLOAD:")))
                assert payload["gpu_inode"] == os.fstat(gpu.fileno()).st_ino
                assert set(payload["core_inodes"]) == {(owner / ".platform/public-install.lock").stat().st_ino}
                assert payload["sentinel"] == "preserved"
                assert {location for _, location in payload["leases"]} == {str(owner)}
                assert CORE_LEASE_ENV not in os.environ
            assert manager.store.registered_processes(job["id"]) == []
            with (tmp_path / "ordinary-gpu-reservation.lock").open("a+") as contender:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        manager.close()


@pytest.mark.parametrize("legacy_gpu", [False, True])
def test_process_guard_keeps_legacy_single_descriptor_protocol(tmp_path, legacy_gpu):
    import select
    import signal
    import subprocess
    import tempfile
    from nanocofold.utils import shell
    lifeline_read, lifeline_write = os.pipe()
    status_read, status_write = os.pipe()
    guard = None
    with (tmp_path / "ordinary-reservation.lock").open("a+") as gpu, tempfile.TemporaryFile() as request, tempfile.TemporaryFile() as output:
        reserved = gpu.fileno() if legacy_gpu else -1
        source = "import os;" + (f"os.fstat({reserved});" if legacy_gpu else "") + "print('legacy protocol preserved')"
        request.write(json.dumps({"argv": [sys.executable, "-I", "-c", source], "env": dict(os.environ)}).encode())
        request.seek(0)
        helper = Path(shell.__file__).with_name("process_guard.py")
        try:
            guard = subprocess.Popen([sys.executable, "-I", str(helper), str(lifeline_read), str(status_write),
                                      str(request.fileno()), str(reserved)],
                                     pass_fds=(lifeline_read, status_write, request.fileno(), *([reserved] if reserved >= 0 else [])),
                                     stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            os.close(lifeline_read); lifeline_read = None
            os.close(status_write); status_write = None
            os.write(lifeline_write, b"G")
            assert select.select([status_read], [], [], 5)[0]
            assert json.loads(os.read(status_read, 4096))["returncode"] == 0
            os.close(lifeline_write); lifeline_write = None
            guard.wait(timeout=5)
            output.seek(0)
            assert b"legacy protocol preserved" in output.read()
        finally:
            if guard is not None and guard.poll() is None:
                os.killpg(guard.pid, signal.SIGKILL)
                guard.wait(timeout=5)
            for descriptor in (lifeline_read, lifeline_write, status_read, status_write):
                if descriptor is not None:
                    os.close(descriptor)
