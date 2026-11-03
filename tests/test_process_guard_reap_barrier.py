"""A private guardian retains reservations through a delayed orphan adoption."""
import pytest

from nanocofold.utils import process_guard as guard


@pytest.mark.parametrize("reaped_first", [False, True])
def test_nonblocking_zero_never_means_no_remaining_children(monkeypatch, reaped_first):
    replies = iter([(123, 9), (0, 0)] if reaped_first else [(0, 0)])
    monkeypatch.setattr(guard.os, "waitpid", lambda *_: next(replies))
    assert guard._reap_children() is False


@pytest.mark.parametrize("reaped_first", [False, True])
def test_only_echild_confirms_complete_reaping(monkeypatch, reaped_first):
    replies = [(123, 9)] if reaped_first else []
    def waitpid(*_):
        if replies:
            return replies.pop()
        raise ChildProcessError
    monkeypatch.setattr(guard.os, "waitpid", waitpid)
    assert guard._reap_children() is True


def test_empty_proc_scan_cannot_release_guardian_before_late_adoption(monkeypatch):
    adopted, killed, sleeps = False, [], []
    def children():
        return [(123, "birth")] if adopted and not killed else []
    def waitpid(*_):
        if not adopted:
            return 0, 0  # The exiting launcher still belongs to the kernel.
        if len(killed) == 1:
            killed.append("reaped")
            return 123, 9
        raise ChildProcessError
    def after_scheduler_turn(seconds):
        nonlocal adopted
        sleeps.append(seconds)
        adopted = True
    monkeypatch.setattr(guard, "_direct_children", children)
    monkeypatch.setattr(guard, "_kill_owned_child", lambda pid, stamp: killed.append((pid, stamp)))
    monkeypatch.setattr(guard.os, "waitpid", waitpid)
    monkeypatch.setattr(guard.time, "sleep", after_scheduler_turn)
    guard._drain_children()
    assert killed == [(123, "birth"), "reaped"]
    assert sleeps == [.01]
