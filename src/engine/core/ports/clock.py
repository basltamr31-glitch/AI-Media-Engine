"""Time as an injected dependency.

Anything that reads the wall clock directly is untestable and produces flaky
tests. A content pipeline is full of time-sensitive logic -- recency decay,
publishing windows, daily boundaries -- so time is a port from day one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Source of the current time."""

    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        ...
