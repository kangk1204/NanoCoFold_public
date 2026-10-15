"""Private stdlib-only group leader, launched with the core Python in isolation.

The worker commits our PID/birth before acknowledging the gate. The lifeline
then owns this entire process group, including when a tool launcher exits first.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading


def main():
    lifeline, status, request, gpu = map(int, sys.argv[1:])
    # EOF before acknowledgement means the owner died before authorizing work.
    if os.read(lifeline, 1) != b"G":
        return

    def kill_group(*_):
        os.killpg(os.getpgrp(), signal.SIGKILL)

    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        # Tools may signal their own group and handle that signal themselves.
        # A caught handler resets on exec; SIG_IGN would leak into the tool.
        if signal.getsignal(number) != signal.SIG_IGN:
            signal.signal(number, lambda *_: None)

    def watch_owner():
        try:
            os.read(lifeline, 1)
        finally:
            kill_group()

    # Watch even during Popen, which can block while the target execs.
    watcher = threading.Thread(target=watch_owner, daemon=True)
    watcher.start()
    try:
        with os.fdopen(request) as handle:
            payload = json.load(handle)
        try:
            child = subprocess.Popen(payload["argv"], env=payload["env"],
                                     close_fds=True, pass_fds=(gpu,) if gpu >= 0 else ())
        except OSError as exc:
            result = {"errno": exc.errno, "strerror": exc.strerror}
        else:
            result = {"returncode": child.wait()}
        os.write(status, json.dumps(result).encode("utf-8") + b"\n")
        # Remain the birth-identified group leader until owned-group cleanup.
        watcher.join()
    finally:
        kill_group()


if __name__ == "__main__":
    main()
