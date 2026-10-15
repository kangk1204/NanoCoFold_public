from __future__ import annotations

import os
import json
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import threading
import weakref
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from ..console_progress import activity, command_label

_DEADLINE = ContextVar("nanocofold_deadline", default=None)
_CANCELLATION = ContextVar("nanocofold_cancellation", default=None)
_OUTPUT_LIMIT = 64 * 1024
_MANAGED_OWNER = None
_CLOSED_THREADS = weakref.WeakKeyDictionary()


class ExecutionCancelled(RuntimeError):
    """A user cancelled the current execution (never a prediction failure)."""


class ExecutionBudgetExceeded(TimeoutError):
    """The enclosing execution deadline expired, distinct from command/network timeouts."""


@contextmanager
def cancellation_signals():
    """Route terminal/session termination through owned-child cleanup."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    def cancelled(number, _frame):
        raise ExecutionCancelled(f"분석 종료 신호를 받았습니다: {signal.Signals(number).name}")
    signals = [signal.SIGTERM, *([signal.SIGHUP] if hasattr(signal, "SIGHUP") else [])]
    previous = {number: signal.getsignal(number) for number in signals}
    try:
        for number in signals:
            # nohup (or the launching application) explicitly requested that
            # hangups be ignored. Preserve it; TERM still cancels and reaps.
            if number == getattr(signal, "SIGHUP", None) and previous[number] == signal.SIG_IGN:
                continue
            signal.signal(number, cancelled)
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


@contextmanager
def cancellation_scope(check):
    """Install a cooperative cancellation predicate for nested commands."""
    token = _CANCELLATION.set(check)
    try:
        yield
    finally:
        _CANCELLATION.reset(token)


@contextmanager
def managed_processes(store, job):
    """Install the detached worker's owner for commands in every worker thread.

    A worker executes one attempt. Threads still alive at scope exit retain a
    closed owner so they cannot launch unmanaged work after completion.
    """
    global _MANAGED_OWNER
    previous = _MANAGED_OWNER
    original_threads = set(threading.enumerate())
    owner = (store, job["id"], job["pid"], job["process_stamp"], True)
    _MANAGED_OWNER = owner
    try:
        yield
    finally:
        for thread in set(threading.enumerate()) - original_threads:
            _CLOSED_THREADS[thread] = (*owner[:4], False)
        _MANAGED_OWNER = previous


@contextmanager
def _command_process(argv, *, cwd, env, stdin, output, inherited, cancellation, start, timeout):
    owner = _CLOSED_THREADS.get(threading.current_thread(), _MANAGED_OWNER)
    if owner is None:
        with subprocess.Popen(argv, cwd=cwd, env=env, stdin=stdin, stdout=output,
                              stderr=subprocess.STDOUT, start_new_session=True, pass_fds=inherited) as process:
            yield process, None
        return
    store, identifier, owner_pid, owner_stamp, active = owner
    if not active:
        raise ExecutionCancelled("종료된 작업에서 외부 명령을 시작할 수 없습니다.")
    from ..platform.jobs import process_stamp
    lifeline_read, lifeline_write = os.pipe()
    status_read, status_write = os.pipe()
    process = None
    stamp = None
    try:
        with tempfile.TemporaryFile(mode="w+b") as request:
            payload = {"argv": argv, "env": dict(os.environ) if env is None else env}
            request.write(json.dumps(payload).encode("utf-8"))
            request.seek(0)
            helper = Path(__file__).with_name("process_guard.py")
            process = subprocess.Popen(
                [sys.executable, "-I", str(helper), str(lifeline_read), str(status_write),
                 str(request.fileno()), str(inherited[0] if inherited else -1)],
                cwd=cwd, env=env, stdin=stdin, stdout=output, stderr=subprocess.STDOUT,
                start_new_session=True, pass_fds=(*inherited, lifeline_read, status_write, request.fileno()))
            os.close(lifeline_read)
            lifeline_read = None
            os.close(status_write)
            status_write = None
            stamp = process_stamp(process.pid)
            store.register_process(identifier, owner_pid, owner_stamp, process.pid, stamp)
            if cancellation and cancellation():
                raise ExecutionCancelled("분석 실행이 취소되었습니다.")
            if timeout is not None and time.monotonic() - start >= timeout:
                raise subprocess.TimeoutExpired(argv, timeout)
            os.write(lifeline_write, b"G")
            yield process, status_read
    finally:
        # Closing the only write end is also the worker-death fallback. Never
        # let Popen.__exit__ wait on a helper still blocked at its launch gate.
        os.close(lifeline_write)
        try:
            if process is not None and process.returncode is None:
                _kill_process_group(process)
        finally:
            for descriptor in (lifeline_read, status_read, status_write):
                if descriptor is not None:
                    os.close(descriptor)
            if process is not None and stamp and process_stamp(process.pid) != stamp:
                store.forget_process(identifier, process.pid, stamp)


def _command_status(descriptor, argv):
    if descriptor is None or not select.select([descriptor], [], [], 0)[0]:
        return None
    data = os.read(descriptor, 4096)
    if not data:
        raise RuntimeError("외부 명령의 소유 프로세스가 상태를 전달하지 못했습니다.")
    status = json.loads(data)
    if "errno" in status:
        raise OSError(status["errno"], status["strerror"], argv[0])
    return status


def remaining_budget(timeout: float | None = None) -> float | None:
    deadline = _DEADLINE.get()
    if deadline is None:
        return timeout
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ExecutionBudgetExceeded("캠페인 계산 시간 한도에 도달했습니다.")
    return min(timeout, remaining) if timeout is not None else remaining


@contextmanager
def execution_budget(seconds: float):
    """Bound all nested external commands, including target/design preparation."""
    token = _DEADLINE.set(time.monotonic() + max(0.0, seconds))
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def _budgeted_process(argv, *, cwd, env, timeout, stdin_text: str | None = None, output_handle=None):
    """Stream output directly to disk and always reap the inference process group.

    stdout on the returned CompletedProcess is a bounded tail for diagnostic/error
    compatibility. Full stdout is kept in output_handle when supplied.
    """
    cancellation = _CANCELLATION.get()
    if cancellation and cancellation():
        raise ExecutionCancelled("분석 실행이 취소되었습니다.")
    # The detached platform worker owns this user-wide GPU reservation. Keep
    # it alive in engine children as well if their parent is forcibly killed.
    gpu_descriptor = (env or os.environ).get("NANOCOFOLD_GPU_LOCK_FD")
    inherited = ()
    if gpu_descriptor is not None:
        descriptor = int(gpu_descriptor)
        os.fstat(descriptor)
        inherited = (descriptor,)
    with tempfile.TemporaryFile(mode="w+b") as captured, tempfile.TemporaryFile(mode="w+b") as stdin:
        output = output_handle if output_handle is not None else captured
        if stdin_text is not None:
            stdin.write(stdin_text.encode("utf-8"))
            stdin.seek(0)
        start = time.monotonic()
        with _command_process(argv, cwd=cwd, env=env, stdin=stdin if stdin_text is not None else None,
                              output=output, inherited=inherited, cancellation=cancellation,
                              start=start, timeout=timeout) as (process, status_descriptor):
            status = None
            try:
                while True:
                    status = _command_status(status_descriptor, argv)
                    if status is not None or _exit_ready(process):
                        break
                    if cancellation and cancellation():
                        raise ExecutionCancelled("분석 실행이 취소되었습니다.")
                    if timeout is not None and time.monotonic() - start >= timeout:
                        raise subprocess.TimeoutExpired(argv, timeout)
                    time.sleep(.05)
            except BaseException as exc:
                _kill_process_group(process)
                if isinstance(exc, subprocess.TimeoutExpired):
                    # Keep bounded evidence after reaping every owned writer.
                    # A printed readiness JSON cannot make a timed-out process
                    # successful, but it helps distinguish loading from a crash.
                    exc.output = _output_tail(output)
                    deadline = _DEADLINE.get()
                    if deadline is not None and time.monotonic() >= deadline:
                        raise ExecutionBudgetExceeded("캠페인 계산 시간 한도에 도달했습니다.") from exc
                raise
            # A launcher can exit successfully as well as fail before its
            # workers. Reap every owned group before publishing its outputs.
            # On Linux WNOWAIT keeps the launcher PID reserved until cleanup,
            # avoiding a recycled numeric process/group identity.
            _kill_process_group(process)
            tail = _output_tail(output)
            return subprocess.CompletedProcess(argv, status["returncode"] if status is not None else process.returncode, tail)


def _output_tail(output):
    output.flush()
    output.seek(0, os.SEEK_END)
    output.seek(max(0, output.tell() - _OUTPUT_LIMIT))
    return output.read().decode("utf-8", errors="replace")


def _exit_ready(process):
    if hasattr(os, "waitid") and hasattr(os, "WNOWAIT"):
        event = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        return event is not None
    return process.poll() is not None


def _kill_process_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        # macOS may deny signalling a group containing only reaped/zombie
        # members. A live child denial remains a real cleanup failure.
        if process.poll() is None:
            raise
    finally:
        process.wait()


class ToolError(RuntimeError):
    """외부 도구 실행 실패."""


@dataclass
class RunResult:
    argv: list[str]
    returncode: int
    log_path: Path | None
    seconds: float
    skipped: bool = False


def env_python(env: str | Path | None) -> Path:
    """venv 디렉터리에서 python 실행 파일을 찾는다. None이면 현재 인터프리터."""
    if env in (None, "", "null"):
        return Path(sys.executable)
    env = Path(env)
    if env.is_file():
        return env
    for candidate in (env / "bin" / "python", env / "bin" / "python3", env / "Scripts" / "python.exe"):
        if candidate.exists():
            return candidate
    raise ToolError(
        f"Python 실행 파일을 찾을 수 없습니다: {env}\n"
        f"envs/setup_*.sh 를 먼저 실행하세요 (nanocofold doctor 로 확인)."
    )


def env_executable(env: str | Path | None, name: str, dry_run: bool = False) -> Path:
    """venv/bin/<name> 실행 파일을 찾는다. dry-run 시 미설치여도 이름만 반환한다."""
    if env not in (None, "", "null"):
        candidate = Path(env) / "bin" / name
        if candidate.exists():
            return candidate
    found = shutil.which(name)
    if found:
        return Path(found)
    if dry_run:
        return Path(name)
    raise ToolError(f"'{name}' 실행 파일을 찾을 수 없습니다. (env={env})")


def run(
    argv: list[str],
    *,
    log_path: Path | None = None,
    cwd: Path | None = None,
    env: dict | None = None,
    timeout: float | None = None,
    check: bool = True,
    dry_run: bool = False,
    quiet: bool = False,
    stdin_text: str | None = None,
) -> RunResult:
    """외부 명령을 실행하고 stdout/stderr를 로그 파일로 저장한다."""
    argv = [str(a) for a in argv]
    printable = " ".join(_quote(a) for a in argv)

    if dry_run:
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(f"# DRY-RUN (미실행)\n$ {printable}\n", encoding="utf-8")
        if not quiet:
            print(f"[dry-run] $ {printable}")
        return RunResult(argv, 0, log_path, 0.0, skipped=True)

    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)

    if not quiet:
        print(f"[run] $ {printable}", flush=True)
    start = time.time()
    deadline = _DEADLINE.get()
    if deadline is not None:
        timeout = remaining_budget(timeout)
    with log_path.open("w+b", buffering=0) if log_path else tempfile.TemporaryFile(mode="w+b") as log:
        log.write(f"$ {printable}\n".encode("utf-8"))
        log.flush()
        try:
            tool = command_label(argv)
            label = "외부 도구: " + tool
            if tool == Path(argv[0]).name and len(argv) > 1 and Path(argv[1]).suffix in {".sh", ".py"}:
                label += " " + Path(argv[1]).name
            with activity(label, log_path):
                proc = _budgeted_process(argv, cwd=str(cwd) if cwd else None, env=env, timeout=timeout,
                                         stdin_text=stdin_text, output_handle=log)
        except BaseException as exc:
            log.seek(0, os.SEEK_END)
            status = f"TIMEOUT after {timeout:.2f}s" if isinstance(exc, subprocess.TimeoutExpired) else type(exc).__name__
            log.write(f"\n# {status}\n".encode("utf-8"))
            log.flush()
            raise
        elapsed = time.time() - start
        log.seek(0, os.SEEK_END)
        log.write(f"\n# exit={proc.returncode} elapsed={elapsed:.1f}s\n".encode("utf-8"))
        log.flush()
    elapsed = time.time() - start
    output = proc.stdout or ""

    if check and proc.returncode != 0:
        tail = "\n".join(output.strip().splitlines()[-30:])
        raise ToolError(
            f"명령 실패 (exit={proc.returncode}): {printable}\n"
            f"로그: {log_path or '-'}\n--- 마지막 출력 ---\n{tail}"
        )
    return RunResult(argv, proc.returncode, log_path, elapsed)


def run_in_env(
    env: str | Path | None,
    argv: list[str],
    extra_env: dict | None = None,
    **kwargs,
) -> RunResult:
    """특정 venv의 환경으로 명령 실행 (bin 디렉터리를 PATH 앞에 둔다)."""
    try:
        python = env_python(env)
    except ToolError:
        if kwargs.get("dry_run", False):
            return run(argv, **kwargs)
        raise
    child_env = dict(os.environ)
    child_env["PATH"] = f"{python.parent}{os.pathsep}{child_env.get('PATH', '')}"
    child_env.setdefault("PYTHONNOUSERSITE", "1")
    for key, value in (extra_env or {}).items():
        if value is None:
            child_env.pop(str(key), None)
        else:
            child_env[str(key)] = str(value)
    return run(argv, env=child_env, **kwargs)


def _quote(token: str) -> str:
    if any(ch in token for ch in " \t'\"()[]{}|&;<>$`"):
        return "'" + token.replace("'", "'\\''") + "'"
    return token


def has_executable(name: str) -> bool:
    return shutil.which(name) is not None


def tool_version(argv: list[str], timeout: float = 20.0) -> str:
    try:
        proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
        first = (proc.stdout or "").strip().splitlines()
        return first[0][:120] if first else "unknown"
    except Exception as exc:  # noqa: BLE001
        return f"unavailable ({exc.__class__.__name__})"
