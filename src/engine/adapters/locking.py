"""A single-run lock, so a scheduler cannot start what is already running.

A daily pipeline that takes longer than its interval is not hypothetical: one
slow model call, one host that times out at 30 seconds per story, and the run
that started at 07:00 is still going when the 08:00 run fires. Two runs at once
would double every request to a publisher's server, spend the cost ceiling
twice, and race each other to write the same story rows.

Implemented as an exclusively-created lock file holding the owning process id.
Deliberately not a database row: the failure this guards against includes "the
database is slow", and a lock that needs the resource it is protecting has a
gap in exactly the case it exists for.

The hard part of file locking is not taking the lock -- it is the lock left
behind by a process that was killed, which would otherwise block every run
forever with no error anyone can act on. So the lock records its owner, and a
lock whose owner is gone is reclaimed rather than obeyed.
"""

from __future__ import annotations

import errno
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from engine.observability.logging import get_logger

logger = get_logger(__name__)


class LockHeldError(RuntimeError):
    """Raised when another live process already holds the lock."""

    def __init__(self, path: Path, owner: LockOwner | None) -> None:
        self.path = path
        self.owner = owner
        detail = (
            f"held by pid {owner.pid} since {owner.acquired_at:%Y-%m-%d %H:%M:%S}Z"
            if owner
            else "held by an unreadable lock file"
        )
        super().__init__(f"another run is in progress: {detail} ({path})")


@dataclass(frozen=True)
class LockOwner:
    """Who holds a lock, and since when."""

    pid: int
    acquired_at: datetime
    hostname: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "pid": self.pid,
                "acquired_at": self.acquired_at.isoformat(),
                "hostname": self.hostname,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> LockOwner | None:
        """Parse a lock file, or return None if it is unreadable.

        A corrupt lock file must not crash the run that found it: the answer to
        "I cannot tell who holds this" is the same as for a stale lock, and it
        is decided by the caller rather than by an exception here.
        """
        try:
            payload: dict[str, Any] = json.loads(raw)
            return cls(
                pid=int(payload["pid"]),
                acquired_at=datetime.fromisoformat(payload["acquired_at"]),
                hostname=str(payload.get("hostname", "")),
            )
        except (ValueError, KeyError, TypeError):
            return None


def process_is_alive(pid: int) -> bool:
    """Whether a process with this id currently exists.

    A process owned by another user counts as alive. Treating "I am not allowed
    to look" as "it is gone" would let two users' runs overlap on a shared
    machine, which is the exact thing the lock exists to prevent.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_is_alive(pid)
    return _posix_process_is_alive(pid)


def _posix_process_is_alive(pid: int) -> bool:
    """``kill(pid, 0)`` performs the existence check and sends no signal."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:  # pragma: no cover -- defensive
        return exc.errno != errno.ESRCH
    return True


def _windows_process_is_alive(pid: int) -> bool:
    """Ask the Win32 API directly, because ``os.kill`` is not a query here.

    On Windows, CPython implements ``os.kill`` for any signal other than the
    console-control events as ``OpenProcess`` followed by ``TerminateProcess``.
    The POSIX idiom ``os.kill(pid, 0)`` therefore does not test whether a
    process exists -- it *kills* it, with exit code 0. Using it here would mean
    a run that checked a stale lock terminated whatever process had since been
    given that id.

    ``OpenProcess`` with query-only access is the actual question. It fails
    with ``ERROR_ACCESS_DENIED`` for a process we may not inspect, which is a
    yes, and with ``ERROR_INVALID_PARAMETER`` for an id that does not exist.
    """
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    error_access_denied = 5
    still_active = 259

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied

    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        # A handle can outlive the process it names, so an exited process is
        # still openable. Only STILL_ACTIVE means it is running.
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


class RunLock:
    """An exclusive lock over one named activity, held for a ``with`` block.

    Not reentrant, and not a substitute for a database transaction: it prevents
    two *runs* from overlapping, and says nothing about concurrent writers
    within one.
    """

    def __init__(self, path: Path, *, now: datetime | None = None) -> None:
        self._path = path
        self._now = now
        self._acquired = False

    @property
    def path(self) -> Path:
        return self._path

    def read_owner(self) -> LockOwner | None:
        """Who holds the lock, if anyone and if it can be read."""
        try:
            return LockOwner.from_json(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except OSError:
            return None

    def acquire(self) -> LockOwner:
        """Take the lock, reclaiming it if its owner is no longer running.

        Raises:
            LockHeldError: If a live process holds it.
        """
        owner = LockOwner(
            pid=os.getpid(),
            acquired_at=self._now or datetime.now(UTC),
            hostname=os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "",
        )

        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._write_exclusively(owner)
        except FileExistsError:
            existing = self.read_owner()
            # An unreadable lock is treated as stale rather than as an
            # impassable wall: the alternative is a run that can never start
            # again and an operator with no way to find out why.
            if existing is not None and process_is_alive(existing.pid):
                raise LockHeldError(self._path, existing) from None

            logger.warning(
                "lock.reclaimed",
                path=str(self._path),
                stale_pid=existing.pid if existing else None,
                reason="owner not running" if existing else "unreadable lock file",
            )
            self._path.unlink(missing_ok=True)
            try:
                self._write_exclusively(owner)
            except FileExistsError:
                # Another process reclaimed it in the same instant. Rare, and
                # correctly resolved by losing: exactly one of us proceeds.
                raise LockHeldError(self._path, self.read_owner()) from None

        self._acquired = True
        logger.debug("lock.acquired", path=str(self._path), pid=owner.pid)
        return owner

    def release(self) -> None:
        """Release the lock, if this instance holds it."""
        if not self._acquired:
            return
        self._path.unlink(missing_ok=True)
        self._acquired = False
        logger.debug("lock.released", path=str(self._path))

    def _write_exclusively(self, owner: LockOwner) -> None:
        """Create the lock file, failing if it already exists.

        ``O_EXCL`` makes the check-and-create one atomic operation. Testing for
        the file and then writing it is the classic race, and the window is
        widest under exactly the load that makes overlapping runs likely.
        """
        descriptor = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(owner.to_json())

    def __enter__(self) -> LockOwner:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
