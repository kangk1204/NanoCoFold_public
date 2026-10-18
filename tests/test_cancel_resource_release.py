"""Bounded real-process resource release; CPU only, no engine/GPU/server.

Linux subreaper cases allocate only 32 MiB in an owned setsid child. All jobs,
locks, logs and sentinel files live in tmp_path. An unrelated control process
must survive. GPU reservation coverage uses an ordinary local flock, never CUDA.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from nanocofold.platform import jobs
from nanocofold.utils import shell

ROOT = Path(__file__).resolve().parents[1]
LINUX = sys.platform.startswith("linux")


def _wait(check, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(.02)
    return bool(check())


def _kill_own(pid, stamp):
    if pid and stamp and jobs.process_stamp(pid) == stamp:
        assert pid != os.getpid()
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def _evidence(name, value):
    directory = os.environ.get("NANOCOFOLD_PROCESS_EVIDENCE_DIR")
    if directory:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        (root / (name + ".json")).write_text(json.dumps(value, indent=2) + "\n")


def _job(root, *, owner=True):
    manager = jobs.JobManager(root)
    job = manager.create({"config": {"run": {"mode": "design", "mock": True},
                                     "target": {"sequence": "ACDEFGHIKLMNPQRSTVWY"}}})
    if owner:
        job = manager.store.update(job["id"], state="running", pid=os.getpid(),
                                   process_stamp=jobs.process_stamp(os.getpid()), started=time.time())
    return manager, job


def _lock_free(path):
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


CHILD = """import json,os,sys,time
from pathlib import Path
memory=bytearray(32*1024*1024)
for index in range(0,len(memory),4096): memory[index]=1
rss=None
if sys.platform.startswith('linux'):
 rss=int(next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('VmRSS:')))
Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),'pgid':os.getpgrp(),'rss_kib':rss,'allocated_bytes':len(memory)}))
time.sleep(30)
"""
LAUNCHER = """import os,subprocess,sys,time
from pathlib import Path
fd=int(os.environ['NANOCOFOLD_GPU_LOCK_FD'])
subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],start_new_session=True,pass_fds=(fd,))
marker=Path(sys.argv[2]);deadline=time.monotonic()+5
while not marker.exists() and time.monotonic()<deadline: time.sleep(.01)
assert marker.exists()
if sys.argv[3]=='wait': time.sleep(30)
else: sys.exit(int(sys.argv[3]))
"""


@pytest.mark.skipif(not LINUX, reason="Linux private guardian subreaper contract")
@pytest.mark.parametrize("outcome", ["0", "7", "cancel"])
def test_unobserved_setsid_child_is_reaped_before_return_and_lock_release(tmp_path, monkeypatch, outcome):
    manager, job = _job(tmp_path)
    marker = tmp_path / "escaped-owned-child.json"
    reservation = tmp_path / "surrogate-gpu.lock"
    keep = tmp_path / "prior-result.bin"
    original = b"preserve existing result\x00\xff"
    keep.write_bytes(original)
    control = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], start_new_session=True)
    control_stamp = jobs.process_stamp(control.pid)
    lock = reservation.open("a+")
    fcntl.flock(lock, fcntl.LOCK_EX)
    monkeypatch.setenv("NANOCOFOLD_GPU_LOCK_FD", str(lock.fileno()))
    monkeypatch.setattr(shell, "_MANAGED_OWNER", None)
    child_pid = child_stamp = None
    try:
        argv = [sys.executable, "-c", LAUNCHER, CHILD, str(marker), "wait" if outcome == "cancel" else outcome]
        with shell.managed_processes(manager.store, job), shell.cancellation_scope(lambda: outcome == "cancel" and marker.exists()):
            if outcome == "cancel":
                with pytest.raises(shell.ExecutionCancelled):
                    shell.run(argv, quiet=True, timeout=8)
            else:
                result = shell.run(argv, quiet=True, timeout=8, check=False)
                assert result.returncode == int(outcome)
        observed = json.loads(marker.read_text())
        child_pid = observed["pid"]
        child_stamp = jobs.process_stamp(child_pid)
        assert observed["pgid"] == child_pid, "fixture really escaped the guardian's process group"
        assert observed["allocated_bytes"] == 32 * 1024 * 1024 and observed["rss_kib"] >= 32 * 1024
        # No supervisor tick or delayed cleanup is allowed to make this pass.
        assert child_stamp is None
        assert not Path(f"/proc/{child_pid}").exists(), "adopted child was actually reaped"
        assert manager.store.registered_processes(job["id"]) == []
        lock.close()
        assert _lock_free(reservation)
        assert jobs.process_stamp(control.pid) == control_stamp
        assert keep.read_bytes() == original
        _evidence("setsid-" + outcome, {"outcome": outcome, "actual_child_rss_kib": observed["rss_kib"],
                  "owned_child_reaped_before_return": True, "supervisor_ticks": 0,
                  "reservation_released": True, "control_preserved": True, "result_preserved": True})
    finally:
        lock.close()
        if marker.exists():
            child_pid = json.loads(marker.read_text())["pid"]
            child_stamp = child_stamp or jobs.process_stamp(child_pid)
        _kill_own(child_pid, child_stamp)
        _kill_own(control.pid, control_stamp)
        control.wait(timeout=5)


@pytest.mark.skipif(not LINUX, reason="Linux private guardian orphan adoption")
def test_owner_sigkill_reclaims_unobserved_setsid_child_without_a_supervisor_tick(tmp_path):
    reservation = tmp_path / "crash-reservation.lock"
    marker = tmp_path / "escaped-owned-child.json"
    control = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], start_new_session=True)
    control_stamp = jobs.process_stamp(control.pid)
    environment = dict(os.environ, PYTHONPATH=str(ROOT), CUDA_VISIBLE_DEVICES="")
    environment.pop("NANOCOFOLD_GPU_LOCK_FD", None)
    log = tmp_path / "crash-owner.log"
    with log.open("wb") as handle:
        owner = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--crash-owner", str(tmp_path)],
                                 cwd=ROOT, env=environment, stdout=handle, stderr=subprocess.STDOUT,
                                 start_new_session=True)
    owner_stamp = jobs.process_stamp(owner.pid)
    child_pid = child_stamp = None
    guardians = []
    try:
        assert _wait(lambda: marker.exists() or owner.poll() is not None)
        assert marker.exists(), log.read_text()
        child_pid = json.loads(marker.read_text())["pid"]
        child_stamp = jobs.process_stamp(child_pid)
        assert child_stamp and not _lock_free(reservation)
        store = jobs.JobStore(tmp_path)
        job = store.get((tmp_path / "job-id").read_text())
        guardians = store.registered_processes(job["id"])
        assert len(guardians) == 1 and not job.get("descendants")
        _kill_own(owner.pid, owner_stamp)
        owner.wait(timeout=5)
        assert _wait(lambda: jobs.process_stamp(child_pid) != child_stamp and
                     all(jobs.process_stamp(pid) != stamp for pid, stamp in guardians) and _lock_free(reservation))
        assert not Path(f"/proc/{child_pid}").exists()
        assert jobs.process_stamp(control.pid) == control_stamp
        _evidence("setsid-owner-sigkill", {"owner_sigkill": True, "supervisor_ticks": 0,
                  "owned_child_reaped": True, "reservation_released": True, "control_preserved": True})
    finally:
        _kill_own(owner.pid, owner_stamp)
        owner.wait(timeout=5)
        _kill_own(child_pid, child_stamp)
        for pid, stamp in guardians:
            _kill_own(pid, stamp)
        _kill_own(control.pid, control_stamp)
        control.wait(timeout=5)


@pytest.mark.parametrize("budget", [False, True])
def test_cancelled_worker_exits_before_terminal_and_releases_rss_and_reservation(tmp_path, monkeypatch, budget):
    manager, job = _job(tmp_path, owner=False)
    root = Path(job["run_dir"])
    root.mkdir(parents=True)
    preserved = root / "preserved-result.bin"
    original = b"saved before cancellation\x00\xff"
    preserved.write_bytes(original)
    reservation = tmp_path / "worker-reservation.lock"
    lock = reservation.open("a+")
    fcntl.flock(lock, fcntl.LOCK_EX)
    identity = os.fstat(lock.fileno())
    manager.store.update(job["id"], state="running", started=time.time(),
                         gpu_lock={"device": identity.st_dev, "inode": identity.st_ino})
    monkeypatch.setattr(manager, "_start_catalog_recovery", lambda: None)
    environment = dict(os.environ, PYTHONPATH=str(ROOT), CUDA_VISIBLE_DEVICES="", NANOCOFOLD_GPU_LOCK_FD=str(lock.fileno()))
    log = tmp_path / "worker.log"
    with log.open("wb") as handle:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--cancel-worker", str(tmp_path), job["id"]],
                                 cwd=ROOT, env=environment, stdout=handle, stderr=subprocess.STDOUT,
                                 pass_fds=(lock.fileno(),), start_new_session=True)
    lock.close()
    stamp = jobs.process_stamp(child.pid)
    assert stamp
    manager.store.update(job["id"], pid=child.pid, pgid=child.pid, process_stamp=stamp)
    try:
        marker = tmp_path / "worker-ready.json"
        assert _wait(lambda: marker.exists() or child.poll() is not None)
        assert marker.exists(), log.read_text()
        observed = json.loads(marker.read_text())
        assert observed["allocated_bytes"] == 32 * 1024 * 1024
        if LINUX:
            assert observed["rss_kib"] >= 32 * 1024
        assert not _lock_free(reservation)
        manager.cancel(job["id"])
        if budget:
            manager.store.update(job["id"], budget_exceeded=True)
        manager._tick()  # sends TERM; this owned worker cooperatively exits
        deadline = time.monotonic() + 5
        while child.poll() is None and time.monotonic() < deadline:
            assert manager.store.get(job["id"])["state"] == "cancelling"
            time.sleep(.02)
        assert child.poll() == 130, log.read_text()
        before = manager.store.get(job["id"])
        assert before["state"] == "cancelling", "a live worker never published terminal cancellation"
        assert before["catalog_pending"] is True
        assert not (tmp_path / "inline-catalog-entered").exists()
        assert jobs.process_stamp(child.pid) is None and _lock_free(reservation)
        manager._tick()
        final = manager.store.get(job["id"])
        assert final["state"] == ("budget_exhausted" if budget else "cancelled")
        assert final["can_resume"] is True and final["catalog_pending"] is True
        assert final["exit_code"] == 130
        assert preserved.read_bytes() == original
        _evidence("worker-resource-" + ("budget" if budget else "cancel"), {
            "worker_exited_before_terminal": True, "allocated_bytes": observed["allocated_bytes"],
            "rss_kib_before_cancel": observed["rss_kib"], "worker_absent_after_cancel": True,
            "reservation_released": True, "inline_catalog_skipped": True,
            "catalog_recovery_pending": True, "final_state": final["state"], "saved_result_preserved": True})
    finally:
        _kill_own(child.pid, stamp)
        child.wait(timeout=5)


def _crash_owner(root):
    manager, job = _job(root)
    (root / "job-id").write_text(job["id"])
    with (root / "crash-reservation.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        os.environ["NANOCOFOLD_GPU_LOCK_FD"] = str(lock.fileno())
        with shell.managed_processes(manager.store, job):
            shell.run([sys.executable, "-c", LAUNCHER, CHILD, str(root / "escaped-owned-child.json"), "wait"], quiet=True, timeout=20)


def _cancel_worker(root, identifier):
    from nanocofold import cli
    from nanocofold.platform.result_catalog import ResultCatalog
    from nanocofold.platform.worker import execute

    def cpu_only(_args):
        memory = bytearray(32 * 1024 * 1024)
        for index in range(0, len(memory), 4096):
            memory[index] = 1
        rss = None
        if LINUX:
            rss = int(next(line.split()[1] for line in Path("/proc/self/status").read_text().splitlines() if line.startswith("VmRSS:")))
        (root / "worker-ready.json").write_text(json.dumps({"allocated_bytes": len(memory), "rss_kib": rss}))
        while True:
            time.sleep(.05)

    def forbidden_inline_catalog(*_args, **_kwargs):
        (root / "inline-catalog-entered").touch()
        time.sleep(10)
        raise RuntimeError("cancelled worker must leave indexing to the dead-worker recovery thread")

    cli.main = cpu_only
    ResultCatalog.register = forbidden_inline_catalog
    return execute(root, identifier)


@pytest.mark.skipif(not LINUX, reason="Linux adopted-child supervisor force cleanup")
def test_force_cancel_reclaims_late_setsid_child_after_initial_descendant_snapshot(tmp_path, monkeypatch):
    manager, job = _job(tmp_path, owner=False)
    root = Path(job["run_dir"])
    root.mkdir(parents=True)
    preserved = root / "keep-before-forced-cancel.bin"
    original = b"keep prior saved result bytes\x00\xff"
    preserved.write_bytes(original)
    reservation = tmp_path / "forced-reservation.lock"
    control = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], start_new_session=True)
    control_stamp = jobs.process_stamp(control.pid)
    environment = dict(os.environ, PYTHONPATH=str(ROOT), CUDA_VISIBLE_DEVICES="")
    environment.pop("NANOCOFOLD_GPU_LOCK_FD", None)
    monkeypatch.setattr(manager, "_start_catalog_recovery", lambda: None)
    manager.store.update(job["id"], state="running", started=time.time())
    log = tmp_path / "forced-worker.log"
    with log.open("wb") as handle:
        owner = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--stubborn-owner", str(tmp_path), job["id"]],
                                 cwd=ROOT, env=environment, stdout=handle, stderr=subprocess.STDOUT,
                                 start_new_session=True)
    owner_stamp = jobs.process_stamp(owner.pid)
    manager.store.update(job["id"], pid=owner.pid, pgid=owner.pid, process_stamp=owner_stamp)
    child_pid = child_stamp = None
    guardians = []
    try:
        assert _wait(lambda: (tmp_path / "launcher-waiting").exists() or owner.poll() is not None)
        assert (tmp_path / "launcher-waiting").exists(), log.read_text()
        guardians = manager.store.registered_processes(job["id"])
        assert len(guardians) == 1
        manager.cancel(job["id"])
        manager._tick()  # records current descendants, then sends ignored TERM
        assert manager.store.get(job["id"])["state"] == "cancelling"
        (tmp_path / "late-child-release").touch()
        marker = tmp_path / "escaped-owned-child.json"
        assert _wait(marker.exists)
        child_pid = json.loads(marker.read_text())["pid"]
        child_stamp = jobs.process_stamp(child_pid)
        assert child_stamp and child_pid not in {pid for pid, _ in manager.store.get(job["id"]).get("descendants", [])}
        assert not _lock_free(reservation)
        # Enter the real force branch without spending the normal 3s grace.
        _, initial_targets = manager.pending_kills[job["id"]]
        manager.pending_kills[job["id"]] = (time.monotonic() - 4, initial_targets)

        def reconciled():
            manager._tick()
            current = manager.store.get(job["id"])
            live = jobs.process_stamp(owner.pid) == owner_stamp or jobs.process_stamp(child_pid) == child_stamp
            if live or any(jobs.process_stamp(pid) == stamp for pid, stamp in guardians):
                assert current["state"] == "cancelling"
            return current["state"] == "cancelled"

        assert _wait(reconciled), log.read_text()
        owner.wait(timeout=5)
        assert jobs.process_stamp(child_pid) != child_stamp
        assert not Path(f"/proc/{child_pid}").exists()
        assert manager.store.registered_processes(job["id"]) == [] and _lock_free(reservation)
        assert jobs.process_stamp(control.pid) == control_stamp and preserved.read_bytes() == original
        _evidence("setsid-forced-late-child", {"initial_term_ignored": True,
                  "setsid_child_created_after_saved_snapshot": True, "child_reaped": True,
                  "guardian_cleanup_acknowledged": True, "state": "cancelled",
                  "reservation_released": True, "control_preserved": True, "saved_result_preserved": True})
    finally:
        _kill_own(owner.pid, owner_stamp)
        owner.wait(timeout=5)
        _kill_own(child_pid, child_stamp)
        for pid, stamp in guardians:
            _kill_own(pid, stamp)
        _kill_own(control.pid, control_stamp)
        control.wait(timeout=5)


def _stubborn_owner(root, identifier):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    store = jobs.JobStore(root)
    assert _wait(lambda: store.get(identifier).get("pid") == os.getpid())
    job = store.get(identifier)
    with (root / "forced-reservation.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        os.environ["NANOCOFOLD_GPU_LOCK_FD"] = str(lock.fileno())
        launcher = """import os,subprocess,sys,time
from pathlib import Path
root=Path(sys.argv[1]);(root/'launcher-waiting').touch()
while not (root/'late-child-release').exists(): time.sleep(.01)
fd=int(os.environ['NANOCOFOLD_GPU_LOCK_FD'])
subprocess.Popen([sys.executable,'-c',sys.argv[2],str(root/'escaped-owned-child.json')],start_new_session=True,pass_fds=(fd,))
time.sleep(30)
"""
        with shell.managed_processes(store, job):
            shell.run([sys.executable, "-c", launcher, str(root), CHILD], quiet=True, timeout=20)


if __name__ == "__main__":
    if sys.argv[1] == "--crash-owner":
        raise SystemExit(_crash_owner(Path(sys.argv[2])))
    if sys.argv[1] == "--cancel-worker":
        raise SystemExit(_cancel_worker(Path(sys.argv[2]), sys.argv[3]))
    if sys.argv[1] == "--stubborn-owner":
        raise SystemExit(_stubborn_owner(Path(sys.argv[2]), sys.argv[3]))
    raise SystemExit("Unsupported owned CPU test subprocess")
