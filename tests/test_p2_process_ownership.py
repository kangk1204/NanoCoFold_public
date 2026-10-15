"""Durable command ownership using only isolated, owned CPU subprocesses.

The worker integration replaces CLI dispatch, while retaining actual
worker.execute, shell.run, persistent process registration, and reconciliation.
No engine, inference, diagnostic probe, GPU, remote host, or existing jobstore
is used. The reservation below is an ordinary advisory lock in tmp_path.
"""
from __future__ import annotations

import contextlib
import errno
import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from nanocofold.platform import jobs
from nanocofold.utils import shell

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def independent_managed_owner(monkeypatch):
    # Every command here belongs to a fresh temporary job, independent of the
    # test runner's environment or another in-process worker fixture.
    monkeypatch.setattr(shell, '_MANAGED_OWNER', None)
    monkeypatch.delenv('NANOCOFOLD_GPU_LOCK_FD', raising=False)


def _job(root):
    manager = jobs.JobManager(root)
    job = manager.create({'config': {'run': {'mode': 'design', 'mock': True},
                                     'target': {'sequence': 'ACDEFGHIKLMNPQRSTVWY'}}})
    return manager, job


def _owner_job(root):
    manager, job = _job(root)
    stamp = jobs.process_stamp(os.getpid())
    assert stamp
    job = manager.store.update(job['id'], state='running', pid=os.getpid(),
                               process_stamp=stamp, started=time.time())
    return manager, job


def _wait(check, *, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(.02)
    return bool(check())


def _kill_owned(pid, stamp, *, group=False):
    if pid and stamp and jobs.process_stamp(pid) == stamp:
        assert pid != os.getpid()
        with contextlib.suppress(ProcessLookupError):
            if group:
                # Registered guardians are independent session/group leaders.
                assert os.getpgid(pid) == pid
                os.killpg(pid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)


def _evidence(name, document):
    destination = os.environ.get('NANOCOFOLD_PROCESS_EVIDENCE_DIR')
    if destination:
        output = Path(destination)
        output.mkdir(parents=True, exist_ok=True)
        (output / (name + '.json')).write_text(json.dumps(document, indent=2) + '\n')


def test_managed_command_preserves_stdin_env_cwd_output_and_returncode(tmp_path, monkeypatch):
    manager, job = _owner_job(tmp_path)
    monkeypatch.setenv('P2_PARENT_ONLY', 'must not enter explicit child environment')
    environment = {'P2_TARGET': 'exact target environment', 'CUDA_VISIBLE_DEVICES': '',
                   'PYTHONUTF8': '1', 'PYTHONCOERCECLOCALE': '0'}
    source = ("import json,os,sys;print('PAYLOAD:'+json.dumps({'stdin':sys.stdin.read(),"
              "'cwd':os.getcwd(),'env':dict(os.environ)},ensure_ascii=False));"
              "print('owned stderr',file=sys.stderr);sys.exit(7)")
    argv = [sys.executable, '-c', source]
    direct = subprocess.run(argv, cwd=tmp_path, env=environment, input='first\nβ-input\n',
                            text=True, capture_output=True, timeout=5)
    with shell.managed_processes(manager.store, job):
        result = shell._budgeted_process(argv, cwd=str(tmp_path), env=environment,
                                         timeout=5, stdin_text='first\nβ-input\n')
    assert result.args == argv and result.returncode == 7
    payload = json.loads(next(line.removeprefix('PAYLOAD:') for line in result.stdout.splitlines()
                              if line.startswith('PAYLOAD:')))
    direct_payload = json.loads(next(line.removeprefix('PAYLOAD:') for line in direct.stdout.splitlines()
                                     if line.startswith('PAYLOAD:')))
    # macOS's Python may add __CF_USER_TEXT_ENCODING; compare with an actual
    # direct exec while ensuring the caller's explicit environment is retained.
    assert payload == direct_payload
    assert payload['stdin'] == 'first\nβ-input\n' and payload['cwd'] == str(tmp_path)
    assert all(payload['env'][key] == value for key, value in environment.items())
    assert 'P2_PARENT_ONLY' not in payload['env']
    assert 'owned stderr' in result.stdout
    assert manager.store.registered_processes(job['id']) == []


def test_managed_command_inherits_stdin_without_consuming_its_launch_gate(tmp_path):
    environment = dict(os.environ, CUDA_VISIBLE_DEVICES='', PYTHONPATH=str(ROOT))
    environment.pop('NANOCOFOLD_GPU_LOCK_FD', None)
    controller = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--stdin', str(tmp_path)],
                                input='inherited stdin β\n', env=environment,
                                capture_output=True, text=True, timeout=10, cwd=ROOT)
    assert controller.returncode == 0, controller.stdout + controller.stderr
    assert 'TARGET:inherited stdin β' in controller.stdout


def test_managed_missing_executable_preserves_errno(tmp_path):
    manager, job = _owner_job(tmp_path)
    missing = str(tmp_path / 'no-such-owned-executable')
    with shell.managed_processes(manager.store, job):
        with pytest.raises(FileNotFoundError) as raised:
            shell.run([missing], timeout=5, quiet=True)
    assert raised.value.errno == errno.ENOENT
    assert raised.value.filename == missing
    assert manager.store.registered_processes(job['id']) == []


@pytest.mark.parametrize('handled', [False, True])
def test_tool_group_term_preserves_its_own_signal_or_handled_returncode(tmp_path, handled):
    manager, job = _owner_job(tmp_path)
    source = 'import os,signal,sys;'
    if handled:
        source += 'signal.signal(signal.SIGTERM,lambda *_:sys.exit(23));'
    source += 'os.killpg(os.getpgrp(),signal.SIGTERM);sys.exit(99)'
    with shell.managed_processes(manager.store, job):
        result = shell._budgeted_process([sys.executable, '-c', source],
                                         cwd=str(tmp_path), env=None, timeout=5)
    assert result.returncode == (23 if handled else -signal.SIGTERM)
    assert manager.store.registered_processes(job['id']) == []


def test_nohup_group_hup_keeps_inherited_ignore_and_tool_returncode(tmp_path):
    environment = dict(os.environ, CUDA_VISIBLE_DEVICES='', PYTHONPATH=str(ROOT))
    environment.pop('NANOCOFOLD_GPU_LOCK_FD', None)
    controller = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--nohup', str(tmp_path)],
                                env=environment, capture_output=True, text=True, timeout=10, cwd=ROOT)
    assert controller.returncode == 0, controller.stdout + controller.stderr
    assert 'HUP_IGN:True' in controller.stdout and 'HUP_SURVIVED' in controller.stdout


def test_threaded_commands_register_before_any_target_starts(tmp_path, monkeypatch):
    manager, job = _owner_job(tmp_path)
    register = manager.store.register_process
    registrations = []
    local = threading.local()

    def record(identifier, owner_pid, owner_stamp, pid, stamp):
        assert identifier == job['id'] and owner_pid == job['pid'] and owner_stamp == job['process_stamp']
        assert jobs.process_stamp(pid) == stamp
        assert not local.marker.exists()
        register(identifier, owner_pid, owner_stamp, pid, stamp)
        registrations.append((pid, stamp))

    monkeypatch.setattr(manager.store, 'register_process', record)

    def command(index):
        marker = tmp_path / ('thread-' + str(index))
        local.marker = marker
        source = 'import sys;from pathlib import Path;Path(sys.argv[1]).write_text("owned")'
        return shell.run([sys.executable, '-c', source, str(marker)], timeout=5, quiet=True)

    with shell.managed_processes(manager.store, job), ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(command, range(3)))
    assert len(registrations) == 3 and len({pid for pid, _ in registrations}) == 3
    assert all(result.returncode == 0 for result in results)
    assert all((tmp_path / ('thread-' + str(index))).read_text() == 'owned' for index in range(3))
    assert manager.store.registered_processes(job['id']) == []


@pytest.mark.parametrize('committed', [False, True])
def test_registration_failure_never_launches_target(tmp_path, monkeypatch, committed):
    manager, job = _owner_job(tmp_path)
    marker = tmp_path / 'must-not-launch'
    register = manager.store.register_process
    guardians = []

    def reject(identifier, owner_pid, owner_stamp, pid, stamp):
        guardians.append((pid, stamp))
        assert not marker.exists()
        if committed:
            register(identifier, owner_pid, owner_stamp, pid, stamp)
        raise RuntimeError('owned registration rejected')

    monkeypatch.setattr(manager.store, 'register_process', reject)
    source = 'import sys;from pathlib import Path;Path(sys.argv[1]).write_text("unexpected")'
    try:
        with shell.managed_processes(manager.store, job), pytest.raises(RuntimeError, match='registration rejected'):
            shell.run([sys.executable, '-c', source, str(marker)], timeout=5, quiet=True)
        assert not marker.exists()
        assert guardians and all(jobs.process_stamp(pid) != stamp for pid, stamp in guardians)
        assert manager.store.registered_processes(job['id']) == []
    finally:
        for pid, stamp in guardians:
            _kill_owned(pid, stamp, group=True)


@pytest.mark.parametrize('returncode', [0, 9])
def test_launcher_exit_cleans_its_live_descendant_before_return(tmp_path, returncode):
    manager, job = _owner_job(tmp_path)
    marker = tmp_path / 'owned-grandchild.json'
    child = ("import json,os,sys,time;from pathlib import Path;"
             "Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),'pgid':os.getpgrp()}));time.sleep(60)")
    launcher = ("import subprocess,sys,time;from pathlib import Path;"
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]);"
                "marker=Path(sys.argv[2]);deadline=time.monotonic()+5;"
                "exec('while not marker.exists() and time.monotonic()<deadline: time.sleep(.01)');"
                "assert marker.exists();sys.exit(int(sys.argv[3]))")
    pid = stamp = None
    try:
        with shell.managed_processes(manager.store, job):
            result = shell.run([sys.executable, '-c', launcher, child, str(marker), str(returncode)],
                               timeout=10, quiet=True, check=False)
        document = json.loads(marker.read_text())
        pid = document['pid']
        stamp = jobs.process_stamp(pid)
        assert result.returncode == returncode
        assert _wait(lambda: jobs.process_stamp(pid) is None)
        assert manager.store.registered_processes(job['id']) == []
    finally:
        _kill_owned(pid, stamp)


def test_closed_managed_scope_rejects_late_thread_launch(tmp_path):
    manager, job = _owner_job(tmp_path)
    marker = tmp_path / 'late-must-not-launch'
    ready, release = threading.Event(), threading.Event()

    def late_command():
        ready.set()
        assert release.wait(timeout=5)
        return shell.run([sys.executable, '-c',
                          'from pathlib import Path;Path(' + repr(str(marker)) + ').write_text("unexpected")'],
                         timeout=5, quiet=True)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with shell.managed_processes(manager.store, job):
            future = pool.submit(late_command)
            assert ready.wait(timeout=5)
        release.set()
        with pytest.raises(shell.ExecutionCancelled):
            future.result(timeout=10)
    assert not marker.exists()


@pytest.mark.parametrize('cancel', [False, True])
def test_unobserved_worker_sigkill_cleans_owned_command_and_reservation(tmp_path, cancel):
    import fcntl
    manager, job = _job(tmp_path)
    run_root = Path(job['run_dir'])
    run_root.mkdir(parents=True)
    sentinel = run_root / 'partial-sentinel.bin'
    original = b'preserve pre-existing partial bytes\x00\xff'
    sentinel.write_bytes(original)
    reservation = tmp_path / 'owned-local-reservation.lock'
    lock = reservation.open('a+')
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    identity = os.fstat(lock.fileno())
    manager.store.update(job['id'], state='running', started=time.time(),
                         gpu_lock={'path': str(reservation), 'device': identity.st_dev, 'inode': identity.st_ino})
    environment = dict(os.environ, CUDA_VISIBLE_DEVICES='', PYTHONPATH=str(ROOT),
                       NANOCOFOLD_GPU_LOCK_FD=str(lock.fileno()))
    log_path = tmp_path / 'owned-worker.log'
    with log_path.open('wb') as log:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', str(tmp_path), job['id']],
                                 env=environment, start_new_session=True, pass_fds=(lock.fileno(),),
                                 stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
    lock.close()
    target_pid = target_stamp = None
    guardians = []
    evidence = {'case': 'cancel' if cancel else 'interrupt', 'workspace': str(tmp_path),
                'worker_pid': child.pid, 'real_worker_execute': True, 'real_shell_run': True,
                'engine': 'owned CPU sleeper only', 'supervisor_observed_before_sigkill': False}
    try:
        owner_stamp = jobs.process_stamp(child.pid)
        assert owner_stamp
        manager.store.update(job['id'], pid=child.pid, pgid=child.pid, process_stamp=owner_stamp)
        marker = tmp_path / 'owned-target.json'
        assert _wait(lambda: marker.exists() or child.poll() is not None, timeout=10)
        assert marker.exists(), log_path.read_text()
        document = json.loads(marker.read_text())
        target_pid = document['pid']
        target_stamp = jobs.process_stamp(target_pid)
        assert target_stamp
        assert document['reservation'] == {'device': identity.st_dev, 'inode': identity.st_ino}
        assert document['extra_fds'] == [document['reservation_fd']]
        guardians = manager.store.registered_processes(job['id'])
        assert len(guardians) == 1
        assert document['pgid'] == guardians[0][0]
        assert not manager.store.get(job['id']).get('descendants')
        evidence.update(worker_stamp=owner_stamp, target_pid=target_pid, target_stamp=target_stamp,
                        registered_guardians=guardians, target_inherited_reservation=True,
                        only_reservation_fd_forwarded=True)
        # No manager tick has ever observed the tool before this abrupt death.
        child.kill()
        child.wait(timeout=5)
        if cancel:
            manager.cancel(job['id'])
        restarted = jobs.JobManager(tmp_path)

        def reconciled():
            restarted._tick()
            return (restarted.store.get(job['id'])['state'] in jobs.TERMINAL
                    and jobs.process_stamp(target_pid) != target_stamp
                    and not restarted.store.registered_processes(job['id']))

        assert _wait(reconciled, timeout=8), log_path.read_text()
        current = restarted.store.get(job['id'])
        assert current['state'] == ('cancelled' if cancel else 'interrupted')
        assert not current.get('result_id')
        assert sentinel.read_bytes() == original
        status = json.loads((run_root / 'platform_status.json').read_text())
        assert status['state'] == current['state']
        with reservation.open('a+') as observer:
            fcntl.flock(observer.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        evidence.update(state=current['state'], target_alive=False, registry_empty=True,
                        local_reservation_released=True, partial_sentinel_preserved=True,
                        portable_status=current['state'], false_completion=False)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        for pid, stamp in guardians:
            _kill_owned(pid, stamp, group=True)
        _kill_owned(target_pid, target_stamp)
        evidence.update(worker_reaped=child.poll() is not None,
                        cleanup_target_birth_absent=not target_pid or jobs.process_stamp(target_pid) != target_stamp,
                        worker_log=log_path.read_text())
        _evidence('process-ownership-worker-' + evidence['case'], evidence)


def _worker_main(root, identifier):
    from nanocofold import cli
    from nanocofold.platform.worker import execute

    def cpu_only(_args):
        source = """import json,os,sys,time
from pathlib import Path
descriptor=int(os.environ['NANOCOFOLD_GPU_LOCK_FD'])
identity=os.fstat(descriptor)
extra=[]
for number in range(3,max(64,descriptor+1)):
    try:
        os.fstat(number)
    except OSError:
        continue
    extra.append(number)
Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),'pgid':os.getpgrp(),
    'reservation':{'device':identity.st_dev,'inode':identity.st_ino},
    'reservation_fd':descriptor,'extra_fds':extra}))
time.sleep(60)
"""
        shell.run([sys.executable, '-c', source, str(root / 'owned-target.json')], quiet=True)
        return 0

    cli.main = cpu_only
    return execute(root, identifier)


def _stdin_main(root):
    manager, job = _owner_job(root)
    with shell.managed_processes(manager.store, job):
        result = shell._budgeted_process([sys.executable, '-c', 'import sys;print("TARGET:"+sys.stdin.read())'],
                                         cwd=str(root), env=None, timeout=5)
    assert result.returncode == 0
    assert manager.store.registered_processes(job['id']) == []
    print(result.stdout)
    return 0


def _nohup_main(root):
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    manager, job = _owner_job(root)
    source = ("import os,signal,sys;print('HUP_IGN:'+str(signal.getsignal(signal.SIGHUP)==signal.SIG_IGN),flush=True);"
              "os.killpg(os.getpgrp(),signal.SIGHUP);print('HUP_SURVIVED',flush=True);sys.exit(17)")
    with shell.managed_processes(manager.store, job):
        result = shell._budgeted_process([sys.executable, '-c', source], cwd=str(root), env=None, timeout=5)
    assert result.returncode == 17
    assert manager.store.registered_processes(job['id']) == []
    print(result.stdout)
    return 0


if __name__ == '__main__':
    if sys.argv[1] == '--worker':
        raise SystemExit(_worker_main(Path(sys.argv[2]), sys.argv[3]))
    if sys.argv[1] == '--stdin':
        raise SystemExit(_stdin_main(Path(sys.argv[2])))
    if sys.argv[1] == '--nohup':
        raise SystemExit(_nohup_main(Path(sys.argv[2])))
    raise SystemExit('Unsupported owned test subprocess mode')
