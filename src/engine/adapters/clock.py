"""Clock implementations."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Real wall-clock time, always timezone-aware UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A clock frozen at a chosen instant.

    Lives in production code rather than the test suite so that any consumer --
    tests, dry runs, backfills, replays of a historical pipeline run -- can pin
    time without importing test helpers.
    """

    def __init__(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            msg = "FixedClock requires a timezone-aware datetime"
            raise ValueError(msg)
        self._moment = moment

    def now(self) -> datetime:
        return self._moment

    def advance(self, *, seconds: float) -> None:
        """Move the clock forward, for testing time-dependent behaviour."""
        from datetime import timedelta

        self._moment = self._moment + timedelta(seconds=seconds)
