"""Tests for the single-run lock.

Two failures matter here and they pull in opposite directions. A lock that is
too weak lets two pipelines run at once, doubling every request to a
publisher's server and spending the cost ceiling twice. A lock that is too
strong outlives the process that took it and blocks every future run with no
error anyone can act on -- which is the worse of the two, because it fails
silently and forever.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from engine.adapters.locking import (
    LockHeldError,
    LockOwner,
    RunLock,
    process_is_alive,
)


def a_lock(tmp_path: Path, name: str = "daily.lock") -> RunLock:
    return RunLock(tmp_path / name)


def write_owner(path: Path, *, pid: int) -> None:
    """Plant a lock file as though another process had taken it."""
    path.write_text(
        LockOwner(pid=pid, acquired_at=datetime.now(UTC)).to_json(),
        encoding="utf-8",
    )


def a_dead_pid() -> int:
    """A process id that certainly does not exist.

    Allocated by taking one beyond the maximum a system will assign; on every
    platform this test runs on, nothing is there.
    """
    return 2**22 - 1


class TestAcquiringAndReleasing:
    def test_a_free_lock_is_taken(self, tmp_path: Path):
        lock = a_lock(tmp_path)

        owner = lock.acquire()

        assert owner.pid == os.getpid()
        assert lock.path.exists()

    def test_releasing_removes_the_file(self, tmp_path: Path):
        lock = a_lock(tmp_path)
        lock.acquire()

        lock.release()

        assert not lock.path.exists()

    def test_the_context_manager_releases_on_the_way_out(self, tmp_path: Path):
        lock = a_lock(tmp_path)

        with lock:
            assert lock.path.exists()

        assert not lock.path.exists()

    def test_the_lock_is_released_even_when_the_run_raises(self, tmp_path: Path):
        """A crashed run must not lock the scheduler out of every future one."""
        lock = a_lock(tmp_path)

        with pytest.raises(RuntimeError), lock:
            raise RuntimeError("the pipeline exploded")

        assert not lock.path.exists()

    def test_releasing_a_lock_this_instance_never_held_does_nothing(self, tmp_path: Path):
        """Otherwise a losing process deletes the winner's lock on its way out."""
        write_owner(tmp_path / "daily.lock", pid=os.getpid())

        a_lock(tmp_path).release()

        assert (tmp_path / "daily.lock").exists()

    def test_the_parent_directory_is_created(self, tmp_path: Path):
        lock = RunLock(tmp_path / "nested" / "deeper" / "daily.lock")

        lock.acquire()

        assert lock.path.exists()


class TestContention:
    def test_a_live_owner_blocks_a_second_run(self, tmp_path: Path):
        write_owner(tmp_path / "daily.lock", pid=os.getpid())

        with pytest.raises(LockHeldError):
            a_lock(tmp_path).acquire()

    def test_the_refusal_names_the_owner(self, tmp_path: Path):
        """An operator has to be able to find the process that is holding it."""
        write_owner(tmp_path / "daily.lock", pid=os.getpid())

        with pytest.raises(LockHeldError) as caught:
            a_lock(tmp_path).acquire()

        assert str(os.getpid()) in str(caught.value)
        assert caught.value.owner is not None

    def test_different_workspaces_do_not_contend(self, tmp_path: Path):
        """One tenant's slow run must not stop another's."""
        a_lock(tmp_path, "one.daily.lock").acquire()

        a_lock(tmp_path, "two.daily.lock").acquire()  # must not raise


class TestStaleLocks:
    def test_a_lock_whose_owner_died_is_reclaimed(self, tmp_path: Path):
        """The failure that would otherwise block every run forever.

        A killed process cannot clean up after itself, so the next run has to
        be able to tell 'held' from 'abandoned'.
        """
        write_owner(tmp_path / "daily.lock", pid=a_dead_pid())

        owner = a_lock(tmp_path).acquire()

        assert owner.pid == os.getpid()

    def test_an_unreadable_lock_is_reclaimed(self, tmp_path: Path):
        """A truncated write must not be an impassable wall.

        'I cannot tell who holds this' has the same answer as a stale lock:
        the alternative is a run that can never start and an operator with no
        way to find out why.
        """
        (tmp_path / "daily.lock").write_text("{not json", encoding="utf-8")

        owner = a_lock(tmp_path).acquire()

        assert owner.pid == os.getpid()

    def test_an_empty_lock_file_is_reclaimed(self, tmp_path: Path):
        (tmp_path / "daily.lock").write_text("", encoding="utf-8")

        assert a_lock(tmp_path).acquire().pid == os.getpid()


class TestOwnerRecord:
    def test_an_owner_round_trips(self):
        original = LockOwner(pid=42, acquired_at=datetime(2026, 8, 8, 7, 0, tzinfo=UTC))

        restored = LockOwner.from_json(original.to_json())

        assert restored == original

    def test_unparseable_json_is_not_an_owner(self):
        assert LockOwner.from_json("{}") is None
        assert LockOwner.from_json("nonsense") is None

    def test_reading_a_missing_lock_returns_nothing(self, tmp_path: Path):
        assert a_lock(tmp_path).read_owner() is None

    def test_reading_reports_who_holds_it(self, tmp_path: Path):
        lock = a_lock(tmp_path)
        lock.acquire()

        owner = lock.read_owner()

        assert owner is not None and owner.pid == os.getpid()


class TestProcessLiveness:
    def test_our_own_process_is_alive(self):
        assert process_is_alive(os.getpid()) is True

    def test_a_missing_process_is_not(self):
        assert process_is_alive(a_dead_pid()) is False

    def test_a_nonsense_pid_is_not_alive(self):
        """Guards the reclaim path against a corrupt but parseable lock file."""
        assert process_is_alive(0) is False
        assert process_is_alive(-1) is False
