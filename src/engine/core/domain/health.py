"""Is the engine actually working?

A single run answers "did this one pass". Unattended operation needs the other
question -- "has it been passing, and has it been happening at all" -- and the
second is not derivable from the first. The failure mode that ends a two-week
unattended stretch is rarely a loud crash; it is a scheduler that quietly
stopped firing, which produces **no failed runs to notice**. Absence has to be
a state the system can report, not a gap in a list.

Pure functions over run history, with no I/O: the assessment is a policy
decision about what counts as healthy, and policy belongs in the domain where
it can be tested without a database, a clock or a scheduler.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from pydantic import Field

from engine.core.domain.base import ImmutableModel
from engine.core.domain.enums import RunStatus
from engine.core.domain.ledger import RunLedger

#: How long without a completed scheduled run before something is wrong.
#: Generous relative to a daily schedule: one missed morning is a blip, two is
#: a pattern, and alerting on the blip is how an operator learns to ignore the
#: alert.
DEFAULT_MAX_AGE_HOURS: Final = 36.0

#: Failure rate over the recent window above which the engine is degraded even
#: though it is still running. Set here rather than at the call site so the
#: threshold is visible next to what it means.
DEFAULT_MAX_FAILURE_RATE: Final = 0.5

#: Run-ledger metadata key marking a rehearsal. A dry run exercises the stages
#: and stores nothing, so it says nothing about whether the engine is producing
#: anything -- and it succeeds reliably, which is precisely what makes it
#: dangerous here.
DRY_RUN_META_KEY: Final = "dry_run"


def is_rehearsal(run: RunLedger) -> bool:
    """Whether this run deliberately stored nothing."""
    return bool(run.meta.get(DRY_RUN_META_KEY))


class HealthStatus(StrEnum):
    """Ordered worst-last, so the overall verdict is a ``max``."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILING = "failing"
    STALE = "stale"
    UNKNOWN = "unknown"


#: Severity order for combining findings. ``UNKNOWN`` outranks nothing: a
#: system that has never run is not broken, it is un-started, and reporting it
#: as a failure on day one trains the operator to dismiss the report.
_SEVERITY: Final[dict[HealthStatus, int]] = {
    HealthStatus.UNKNOWN: 0,
    HealthStatus.HEALTHY: 1,
    HealthStatus.DEGRADED: 2,
    HealthStatus.FAILING: 3,
    HealthStatus.STALE: 4,
}


class HealthFinding(ImmutableModel):
    """One thing that is or is not right, with the number behind it."""

    code: str = Field(description="Stable key. Counted and alerted on, so it must not vary.")
    status: HealthStatus
    summary: str = Field(description="One line, written for whoever is woken up by it.")


class HealthReport(ImmutableModel):
    """The verdict, and everything it was based on."""

    status: HealthStatus
    findings: list[HealthFinding] = Field(default_factory=list)
    runs_considered: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)
    last_run_at: datetime | None = None
    hours_since_last_run: float | None = None

    @property
    def is_healthy(self) -> bool:
        return self.status in {HealthStatus.HEALTHY, HealthStatus.UNKNOWN}

    @property
    def worst(self) -> HealthFinding | None:
        """The finding that decided the verdict, for a one-line alert."""
        if not self.findings:
            return None
        return max(self.findings, key=lambda finding: _SEVERITY[finding.status])


def assess(
    runs: list[RunLedger],
    *,
    now: datetime,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    max_failure_rate: float = DEFAULT_MAX_FAILURE_RATE,
) -> HealthReport:
    """Judge a workspace's recent scheduled runs.

    ``runs`` should already be filtered to the trigger and window of interest;
    this decides what the numbers *mean*, not which rows to read. Whether a
    rehearsal counts is a question about meaning, so it is settled here.
    """
    if not runs:
        return HealthReport(
            status=HealthStatus.UNKNOWN,
            findings=[
                HealthFinding(
                    code="no_runs",
                    status=HealthStatus.UNKNOWN,
                    summary=(
                        "No scheduled runs recorded. Either the schedule was "
                        "never set up, or it has never fired."
                    ),
                )
            ],
        )

    # Dry runs are excluded from every number below. A rehearsal stores nothing
    # and succeeds reliably, so counting one as evidence of health means a cron
    # line left on --dry-run reports green forever while producing nothing --
    # the exact failure this module exists to catch, wearing a success badge.
    rehearsals = [run for run in runs if is_rehearsal(run)]
    real = [run for run in runs if not is_rehearsal(run)]

    if not real:
        return HealthReport(
            status=HealthStatus.FAILING,
            findings=[
                HealthFinding(
                    code="only_dry_runs",
                    status=HealthStatus.FAILING,
                    summary=(
                        f"All {len(rehearsals)} recent scheduled run(s) were dry runs, "
                        "which store nothing. Remove --dry-run from the scheduled command."
                    ),
                )
            ],
            runs_considered=0,
            last_run_at=max(run.started_at for run in rehearsals),
        )

    ordered = sorted(real, key=lambda run: run.started_at, reverse=True)
    latest = ordered[0]
    age_hours = (now - latest.started_at).total_seconds() / 3600.0
    failures = [run for run in ordered if run.status is RunStatus.FAILED]
    failure_rate = len(failures) / len(ordered)

    findings: list[HealthFinding] = []

    if age_hours > max_age_hours:
        # First, and worst. A stale schedule makes every other number below
        # stale too: a perfect success rate from last week says nothing about
        # whether anything ran this morning.
        findings.append(
            HealthFinding(
                code="stale",
                status=HealthStatus.STALE,
                summary=(
                    f"No scheduled run for {age_hours:.0f}h "
                    f"(expected within {max_age_hours:.0f}h). The scheduler may have stopped."
                ),
            )
        )

    if latest.status is RunStatus.FAILED:
        findings.append(
            HealthFinding(
                code="last_run_failed",
                status=HealthStatus.FAILING,
                summary=f"The most recent run failed: {latest.error or 'no reason recorded'}",
            )
        )

    if failures and failure_rate > max_failure_rate:
        findings.append(
            HealthFinding(
                code="high_failure_rate",
                status=HealthStatus.FAILING,
                summary=(
                    f"{len(failures)} of {len(ordered)} recent runs failed ({failure_rate:.0%})."
                ),
            )
        )
    elif failures:
        # Intermittent failure is worth surfacing without waking anyone: a
        # network that drops one morning in five is a real signal, and it only
        # becomes visible if the healthy case is not reported as merely fine.
        findings.append(
            HealthFinding(
                code="intermittent_failures",
                status=HealthStatus.DEGRADED,
                summary=f"{len(failures)} of {len(ordered)} recent runs failed.",
            )
        )

    if not findings:
        findings.append(
            HealthFinding(
                code="ok",
                status=HealthStatus.HEALTHY,
                summary=f"{len(ordered)} scheduled run(s), none failed.",
            )
        )

    return HealthReport(
        status=max(
            (finding.status for finding in findings),
            key=lambda status: _SEVERITY[status],
        ),
        findings=findings,
        runs_considered=len(ordered),
        failures=len(failures),
        last_run_at=latest.started_at,
        hours_since_last_run=age_hours,
    )


def window_start(now: datetime, *, days: int) -> datetime:
    """The earliest run a health assessment should consider."""
    return now - timedelta(days=days)
