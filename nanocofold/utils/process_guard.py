"""Private stdlib-only command owner, launched with the core Python in isolation.

The worker commits our PID/birth before acknowledging the gate. On Linux this
private guardian also adopts orphaned descendants, including tools which create
new sessions. Results and inherited reservations remain held until cleanup.
"""
from __future__ import annotations

import ctypes
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


def _linux_subreaper():
    if not sys.platform.startswith("linux"):
        return False
    # This is private to the short-lived guardian, never the web server/worker.
    # Fail before tool spawn if the kernel cannot guarantee orphan adoption.
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))
    return True


def _owned_stamp(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if int(fields[1]) == os.getpid() else None
    except (FileNotFoundError, ProcessLookupError):
        return None


def _direct_children():
    children = set()
    # Linux records children by creating thread. Adopted children can be
    # reassigned to another thread in this private guardian's thread group.
    for task in Path("/proc/self/task").iterdir():
        try:
            children.update(int(value) for value in (task / "children").read_text().split())
        except (FileNotFoundError, ProcessLookupError):
            pass
    return [(pid, stamp) for pid in children if (stamp := _owned_stamp(pid)) is not None]


def _kill_owned_child(pid, stamp):
    descriptor = None
    try:
        if _owned_stamp(pid) != stamp:
            return
        if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
            descriptor = os.pidfd_open(pid)
            if _owned_stamp(pid) == stamp:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
        elif _owned_stamp(pid) == stamp:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def main():
    lifeline, status, request, *reservations = map(int, sys.argv[1:])
    reservations = tuple(dict.fromkeys(fd for fd in reservations if fd >= 0))
    subreaper = _linux_subreaper()
    # EOF before acknowledgement means the owner died before authorizing work.
    if os.read(lifeline, 1) != b"G":
        return

    cleanup_lock = threading.Lock()

    def cleanup_children():
        if not subreaper:
            return
        with cleanup_lock:
            while True:
                children = _direct_children()
                for pid, stamp in children:
                    _kill_owned_child(pid, stamp)
                # Only children of this private guardian are eligible for reap.
                # Killing a parent adopts its children; repeat until all levels
                # have been killed/reaped, irrespective of their session/group.
                while True:
                    try:
                        reaped, _ = os.waitpid(-1, os.WNOHANG)
                    except ChildProcessError:
                        reaped = 0
                    if not reaped:
                        break
                if not _direct_children():
                    return
                time.sleep(.01)

    def kill_group(*_):
        cleanup_children()
        os.killpg(os.getpgrp(), signal.SIGKILL)

    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        # A tool can signal its own group and handle that signal itself. The
        # lifeline, rather than those signals, authorizes guardian shutdown.
        if signal.getsignal(number) != signal.SIG_IGN:
            signal.signal(number, lambda *_: None)

    # A restarting supervisor cannot close another process's owner pipe.
    # Its birth-verified guardian-only signal wakes the same cleanup watcher.
    # The signal handler never reenters cleanup or its lock on the main thread.
    control_read, control_write = os.pipe()
    os.set_blocking(control_write, False)

    def request_cleanup(*_):
        try:
            os.write(control_write, b"K")
        except (BlockingIOError, OSError):
            pass  # An existing wake-up or process exit already owns cleanup.

    signal.signal(signal.SIGUSR1, request_cleanup)

    def watch_owner():
        try:
            select.select([lifeline, control_read], [], [])
        finally:
            kill_group()

    watcher = threading.Thread(target=watch_owner, daemon=True)
    watcher.start()
    try:
        with os.fdopen(request) as handle:
            payload = json.load(handle)
        try:
            child = subprocess.Popen(payload["argv"], env=payload["env"],
                                     close_fds=True, pass_fds=reservations)
        except OSError as exc:
            result = {"errno": exc.errno, "strerror": exc.strerror}
        else:
            result = {"returncode": child.wait()}
        # Do not publish success or let the worker release its reservation while
        # a short-lived launcher has left an unobserved setsid child behind.
        cleanup_children()
        os.write(status, json.dumps(result).encode("utf-8") + b"\n")
        watcher.join()
    finally:
        kill_group()


if __name__ == "__main__":
    main()
