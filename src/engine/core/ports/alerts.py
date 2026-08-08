"""Where a failure goes when nobody is watching the terminal.

An unattended run that fails has to reach a human somehow, and "it is in the
log" is only true if somebody reads the log. This port exists so that *where*
alerts go is a deployment choice -- a file, a log, a webhook, a paging service --
without the code that detects a problem knowing anything about it.

Kept deliberately narrow. An alert is a fact that something is wrong, not a
delivery mechanism with retries, deduplication and escalation policies; those
belong to whatever the sink hands off to, all of which already exist and none
of which this project should reimplement.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from engine.core.domain.base import ImmutableModel, utcnow


class AlertSeverity(StrEnum):
    """How much attention this deserves.

    Two levels, not five. A severity scale finer than the number of distinct
    responses available is a scale nobody calibrates: either somebody looks
    now, or they look when they next sit down.
    """

    WARNING = "warning"
    CRITICAL = "critical"


class Alert(ImmutableModel):
    """Something went wrong, described well enough to act on."""

    code: str = Field(
        description="Stable key, e.g. 'run_failed'. Grouped and counted, so it must not vary."
    )
    severity: AlertSeverity = AlertSeverity.WARNING
    summary: str = Field(description="One line. Assume it is read on a phone, at night.")
    detail: str = Field(default="", description="The full error, stack or counts.")
    workspace_slug: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    raised_at: datetime = Field(default_factory=utcnow)

    @property
    def is_critical(self) -> bool:
        return self.severity is AlertSeverity.CRITICAL


@runtime_checkable
class AlertSink(Protocol):
    """Somewhere alerts go."""

    def send(self, alert: Alert) -> None:
        """Deliver one alert.

        Implementations must not raise. An alerting channel that breaks the run
        it was reporting on turns one failure into two, and the second one has
        nowhere to be reported.
        """
        ...
