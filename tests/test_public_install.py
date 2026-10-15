"""Bootstrap contracts without network access, apt changes, or model execution."""
import argparse
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("public_bootstrap", ROOT / "scripts/install_runtime.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)
GPU = "GPU-12345678-1234-1234-1234-123456789abc"
GPU2 = "GPU-87654321-1234-1234-1234-123456789abc"


@pytest.fixture
def mock_gpu(monkeypatch):
    monkeypatch.delenv("NANOCOFOLD_ALLOWED_GPUS", raising=False)
    def set_rows(rows):
        monkeypatch.setattr(bootstrap.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=rows))
    set_rows(f"0, {GPU}, 580.99, GPU model, 16384\n")
    return set_rows


def test_single_gpu_is_selected_with_driver_and_uuid(mock_gpu):
    assert bootstrap.select_gpu(None)["uuid"] == GPU
    assert bootstrap.select_gpu(GPU.lower().replace("gpu-", "GPU-"))["driver"] == "580.99"


@pytest.mark.parametrize("rows", ["", f"0, {GPU}, 580.99, First, 16384\n1, {GPU2}, 580.99, Second, 16384\n"])
def test_gpu_auto_requires_exactly_one_device(mock_gpu, rows):
    mock_gpu(rows)
    with pytest.raises(ValueError, match="여러 개이거나 없습니다"):
        bootstrap.select_gpu(None)


def test_multiple_gpu_explicit_selection_does_not_fallback(mock_gpu):
    mock_gpu(f"0,{GPU},580.99,First,16384\n1,{GPU2},580.99,Second,16384\n")
    assert bootstrap.select_gpu(GPU2)["index"] == "1"
    with pytest.raises(ValueError, match="전체 GPU-UUID"):
        bootstrap.select_gpu("0")


@pytest.mark.parametrize("driver", ["579.99", "550.120", "N/A"])
def test_old_unknown_driver_fails(mock_gpu, driver):
    mock_gpu(f"0,{GPU},{driver},GPU,16384\n")
    with pytest.raises(ValueError, match="580"):
        bootstrap.select_gpu(GPU)


def test_gpu_allowlist_blocks_selection(mock_gpu, monkeypatch):
    monkeypatch.setenv("NANOCOFOLD_ALLOWED_GPUS", GPU2)
    with pytest.raises(ValueError, match="허용 GPU"):
        bootstrap.select_gpu(GPU)
    monkeypatch.setenv("NANOCOFOLD_ALLOWED_GPUS", "0")
    assert bootstrap.select_gpu(GPU)["uuid"] == GPU


def test_nvidia_failure_is_not_bypassed(mock_gpu, monkeypatch):
    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(255, args[0], stderr="Driver/library version mismatch")
    monkeypatch.setattr(bootstrap.subprocess, "run", failed)
    with pytest.raises(subprocess.CalledProcessError):
        bootstrap.select_gpu(GPU)


def test_core_check_is_read_only_and_has_no_gpu_requirement(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "ensure_uv", lambda *_: pytest.fail("no install"))
    monkeypatch.setattr(bootstrap, "select_gpu", lambda *_: pytest.fail("no GPU"))
    args = bootstrap.parser().parse_args(["--profile", "core", "--workspace", str(tmp_path / "absent"), "--check-host"])
    bootstrap.install(args)
    assert list(tmp_path.iterdir()) == []


def test_shared_lock_keeps_inode_and_rejects_competing_owner(tmp_path):
    path = tmp_path / "gpu.lock"
    with bootstrap.lock(path):
        inode = path.stat().st_ino
        with pytest.raises(ValueError, match="다른 설치"):
            with bootstrap.lock(path):
                pytest.fail("lock entered twice")
    with bootstrap.lock(path):
        assert path.stat().st_ino == inode
    assert path.exists()


def test_symlink_lock_does_not_touch_target(tmp_path):
    source = tmp_path / "user-data"
    source.write_bytes(b"preserve")
    path = tmp_path / "gpu.lock"
    path.symlink_to(source)
    with pytest.raises(OSError):
        with bootstrap.lock(path):
            pytest.fail("unsafe lock")
    assert source.read_bytes() == b"preserve"


@pytest.mark.parametrize("state", ["queued", "running", "cancelling"])
def test_active_jobs_prevent_environment_updates(tmp_path, state):
    database = tmp_path / ".nanocofold/jobs.sqlite3"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (state TEXT)")
        connection.execute("INSERT INTO jobs VALUES (?)", (state,))
    with pytest.raises(ValueError, match="대기/실행"):
        bootstrap.reject_active_jobs(tmp_path)


def test_finished_jobs_allow_setup_without_modifying_database(tmp_path):
    database = tmp_path / ".nanocofold/jobs.sqlite3"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (state TEXT)")
        connection.execute("INSERT INTO jobs VALUES ('complete')")
    before = database.read_bytes()
    bootstrap.reject_active_jobs(tmp_path)
    assert database.read_bytes() == before


def test_registered_terminal_command_blocks_update_until_dead(tmp_path, monkeypatch):
    from nanocofold.platform.jobs import JobStore
    store = JobStore(tmp_path)
    with store.connect() as connection:
        connection.execute("INSERT INTO job_processes VALUES ('old',12,'child',11,'owner')")
    before = store.db.read_bytes()
    monkeypatch.setattr(bootstrap, "_process_stamp", lambda pid: "child" if pid == 12 else None)
    with pytest.raises(ValueError, match="owned command"):
        bootstrap.reject_active_jobs(tmp_path)
    monkeypatch.setattr(bootstrap, "_process_stamp", lambda _pid: None)
    bootstrap.reject_active_jobs(tmp_path)
    assert store.db.read_bytes() == before


@pytest.fixture
def mock_install(tmp_path, monkeypatch):
    root = tmp_path / "source with spaces"
    root.mkdir()
    workspace = tmp_path / "workspace with spaces"
    monkeypatch.setattr(bootstrap, "ROOT", root)
    monkeypatch.setattr(bootstrap, "check_host", lambda *args: None)
    monkeypatch.setattr(bootstrap, "ensure_uv", lambda *_: "/uv with spaces/uv")
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if "setup" in argv:
            bootstrap.save(workspace / ".platform/setup-core.json", {"status": "complete", "offline_ready": True})
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    args = argparse.Namespace(workspace=workspace, profile="core", gpu=None, check_host=False)
    return args, root, calls


def test_success_requires_actual_setup_and_records_workspace(mock_install):
    args, root, calls = mock_install
    bootstrap.install(args)
    receipt = json.loads((root / ".nanocofold/install-location.json").read_text())
    assert receipt["workspace"] == str(args.workspace)
    assert receipt["profile"] == "core" and receipt["gpu"] is None
    attempt = json.loads(Path(receipt["attempt_receipt"]).read_text())
    assert attempt["status"] == "complete"
    assert calls[0][0][:4] == ["/uv with spaces/uv", "venv", "--python", "3.12.13"]
    assert any("--require-hashes" in argv for argv, _ in calls)
    assert calls[-1][0][-3:] == ["--profile", "core", "--yes"]


@pytest.mark.parametrize("failure", ["pip", "setup", "readiness"])
def test_failure_retains_attempt_without_publishing_location(mock_install, monkeypatch, failure):
    args, root, calls = mock_install
    original = bootstrap.subprocess.run
    def run(argv, **kwargs):
        if failure in argv:
            raise subprocess.CalledProcessError(1, argv)
        result = original(argv, **kwargs)
        if failure == "readiness" and "setup" in argv:
            bootstrap.save(args.workspace / ".platform/setup-core.json", {"status": "complete", "offline_ready": False})
        return result
    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    with pytest.raises((subprocess.CalledProcessError, ValueError)):
        bootstrap.install(args)
    assert not (root / ".nanocofold/install-location.json").exists()
    attempts = list((args.workspace / ".platform/install-attempts").glob("*.json"))
    assert len(attempts) == 1 and json.loads(attempts[0].read_text())["status"] == "failed"


def test_repeat_preserves_previous_receipt(mock_install):
    args, root, calls = mock_install
    bootstrap.install(args)
    first = json.loads((root / ".nanocofold/install-location.json").read_text())
    original = Path(first["attempt_receipt"]).read_bytes()
    bootstrap.install(args)
    assert Path(first["attempt_receipt"]).read_bytes() == original
    assert len(list((args.workspace / ".platform/install-attempts").glob("*.json"))) == 2


def test_symlink_core_environment_is_rejected_before_install(mock_install, tmp_path):
    args, _, calls = mock_install
    args.workspace.mkdir()
    target = tmp_path / "existing environment"
    target.mkdir()
    (args.workspace / ".venv-dev").symlink_to(target)
    with pytest.raises(ValueError, match="심볼릭 링크 core"):
        bootstrap.install(args)
    assert calls == [] and list(target.iterdir()) == []


def test_all_profile_covers_every_engine_used_by_four_modes():
    from nanocofold.install import PROFILES
    assert set(PROFILES["all"]) == {"antibody", "boltz2", "chai1", "rfantibody", "boltzgen", "esm", "colabfold", "opendde"}
    assert len(PROFILES["all"]) == 8
    for name in ("chai1", "boltzgen", "colabfold"):
        assert "matplotlib==" in (ROOT / "nanocofold/_data/locks" / f"{name}.txt").read_text()


@pytest.mark.parametrize("arguments", [["--gpu"], ["--workspace"], ["--profile", "unknown"], ["--unknown"]])
def test_shell_arguments_fail_before_any_host_mutation(arguments):
    result = subprocess.run(["bash", str(ROOT / "install.sh"), *arguments], capture_output=True)
    assert result.returncode == 2


def test_shell_help_and_syntax_are_available_without_python_or_gpu():
    for script in ("install.sh", "start.sh"):
        assert subprocess.run(["bash", "-n", str(ROOT / script)], capture_output=True).returncode == 0
    result = subprocess.run(["bash", str(ROOT / "install.sh"), "--help"], capture_output=True, text=True)
    assert result.returncode == 0 and "--check-host" in result.stdout


def test_unmanaged_core_environment_is_preserved(mock_install):
    args, _, calls = mock_install
    python = args.workspace / ".venv-dev/bin/python"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"existing user binary")
    with pytest.raises(ValueError, match="관리하지 않는 기존 core"):
        bootstrap.install(args)
    assert python.read_bytes() == b"existing user binary" and calls == []


def test_registered_core_can_be_resumed_without_replacing_marker_identity(mock_install):
    args, _, calls = mock_install
    python = args.workspace / ".venv-dev/bin/python"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"existing managed binary")
    marker = args.workspace / ".platform/public-core-install.json"
    bootstrap.save(marker, {"workspace": str(args.workspace), "python": str(python), "status": "installing"})
    bootstrap.install(args)
    assert not any("venv" in argv for argv, _ in calls)
    assert json.loads(marker.read_text())["status"] == "complete"
    assert all(kwargs["cwd"] == args.workspace for _, kwargs in calls)


def test_gpu_uuid_canonicalization_matches_application_lock(mock_gpu):
    from nanocofold.platform.jobs import gpu_identifier
    mock_gpu(f"0,{GPU.upper()},580.99,GPU,16384\n")
    selected = bootstrap.select_gpu(GPU)
    assert selected["uuid"] == gpu_identifier(GPU.upper())


def test_gpu_lock_fd_is_passed_to_setup_without_reopening(mock_install, monkeypatch, tmp_path):
    args, _, calls = mock_install
    args.profile = "all"
    monkeypatch.setattr(bootstrap, "check_host", lambda *a: {"uuid": GPU})
    monkeypatch.setenv("NANOCOFOLD_GPU_LOCK_DIR", str(tmp_path / "shared locks"))
    original = bootstrap.subprocess.run
    identities = []
    def run(argv, **kwargs):
        environment = kwargs["env"]
        descriptor = int(environment["NANOCOFOLD_GPU_LOCK_FD"])
        assert kwargs["pass_fds"] == (descriptor,)
        assert environment["CUDA_VISIBLE_DEVICES"] == GPU
        import os
        info = os.fstat(descriptor)
        identities.append((info.st_dev, info.st_ino))
        with pytest.raises(ValueError, match="다른 설치"):
            with bootstrap.lock(tmp_path / "shared locks" / ("gpu-" + GPU + ".lock")):
                pytest.fail("reservation was dropped")
        result = original(argv, **kwargs)
        if "setup" in argv:
            bootstrap.save(args.workspace / ".platform/setup-all.json", {"status": "complete", "offline_ready": True})
        return result
    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    bootstrap.install(args)
    assert len(set(identities)) == 1
    assert (tmp_path / "shared locks" / ("gpu-" + GPU + ".lock")).is_file()
