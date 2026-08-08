"""Tests for the health assessment behind unattended operation.

The case that drives the design: a scheduler that stops firing produces no
failed runs, so a system that only reports failures reports nothing at all
while being completely broken. Staleness therefore has to outrank every other
finding, and "never run" has to be distinguishable from "stopped running".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from engine.core.domain.enums import RunStatus
from engine.core.domain.health import (
    HealthStatus,
    assess,
    window_start,
)
from engine.core.domain.ledger import RunLedger

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def a_run(
    *,
    hours_ago: float = 1.0,
    status: RunStatus = RunStatus.SUCCEEDED,
    error: str | None = None,
) -> RunLedger:
    return RunLedger(
        workspace_id="ws_test",
        pipeline="daily_content",
        status=status,
        trigger="schedule",
        started_at=NOW - timedelta(hours=hours_ago),
        finished_at=NOW - timedelta(hours=hours_ago),
        error=error,
    )


class TestNothingHasRun:
    def test_no_history_is_unknown_not_broken(self):
        """Day one is not an incident, and reporting it as one trains
        the operator to dismiss the report."""
        report = assess([], now=NOW)

        assert report.status is HealthStatus.UNKNOWN
        assert report.is_healthy

    def test_it_says_which_of_the_two_reasons_applies(self):
        report = assess([], now=NOW)

        assert report.findings[0].code == "no_runs"


class TestHealthy:
    def test_recent_successes_are_healthy(self):
        report = assess([a_run(hours_ago=1), a_run(hours_ago=25)], now=NOW)

        assert report.status is HealthStatus.HEALTHY
        assert report.failures == 0

    def test_the_counts_are_reported(self):
        report = assess([a_run(hours_ago=1), a_run(hours_ago=25)], now=NOW)

        assert report.runs_considered == 2
        assert report.hours_since_last_run == 1.0


class TestStaleness:
    def test_a_silent_scheduler_is_stale(self):
        """Every run passed; nothing has run since Tuesday."""
        report = assess([a_run(hours_ago=100)], now=NOW)

        assert report.status is HealthStatus.STALE

    def test_staleness_outranks_a_perfect_record(self):
        """A success rate from last week says nothing about this morning."""
        runs = [a_run(hours_ago=hours) for hours in (100, 124, 148)]

        report = assess(runs, now=NOW)

        assert report.status is HealthStatus.STALE
        assert report.failures == 0

    def test_the_threshold_is_configurable(self):
        report = assess([a_run(hours_ago=10)], now=NOW, max_age_hours=5)

        assert report.status is HealthStatus.STALE

    def test_one_missed_morning_is_not_yet_stale(self):
        """Alerting on the blip is how an operator learns to ignore alerts."""
        report = assess([a_run(hours_ago=30)], now=NOW)

        assert report.status is HealthStatus.HEALTHY


class TestFailures:
    def test_a_failed_last_run_is_failing(self):
        report = assess([a_run(status=RunStatus.FAILED, error="model down")], now=NOW)

        assert report.status is HealthStatus.FAILING

    def test_the_reason_reaches_the_finding(self):
        """It is what gets read on a phone, at night."""
        report = assess([a_run(status=RunStatus.FAILED, error="model down")], now=NOW)

        assert report.worst is not None
        assert "model down" in report.worst.summary

    def test_a_failure_with_no_recorded_reason_still_reads(self):
        report = assess([a_run(status=RunStatus.FAILED)], now=NOW)

        assert report.worst is not None
        assert "no reason recorded" in report.worst.summary

    def test_occasional_failure_is_degraded_not_failing(self):
        """A network that drops one morning in five is real but not urgent."""
        runs = [a_run(hours_ago=1)] + [
            a_run(hours_ago=hours, status=RunStatus.FAILED) for hours in (25,)
        ]
        runs += [a_run(hours_ago=hours) for hours in (49, 73)]

        report = assess(runs, now=NOW)

        assert report.status is HealthStatus.DEGRADED

    def test_mostly_failing_is_failing(self):
        runs = [a_run(hours_ago=1)] + [
            a_run(hours_ago=hours, status=RunStatus.FAILED) for hours in (25, 49)
        ]

        report = assess(runs, now=NOW)

        assert report.status is HealthStatus.FAILING

    def test_a_recovered_engine_is_not_failing_on_old_news(self):
        """The most recent run passed; one failure three days ago is history."""
        runs = [a_run(hours_ago=1)] + [a_run(hours_ago=hours) for hours in (25, 49)]
        runs.append(a_run(hours_ago=73, status=RunStatus.FAILED))

        report = assess(runs, now=NOW)

        assert report.status is HealthStatus.DEGRADED
        assert report.is_healthy is False


class TestOrderingAndInputs:
    def test_runs_need_not_arrive_sorted(self):
        """The caller filters rows; ordering is this function's problem."""
        report = assess([a_run(hours_ago=100), a_run(hours_ago=1)], now=NOW)

        assert report.hours_since_last_run == 1.0
        assert report.status is HealthStatus.HEALTHY

    def test_the_worst_finding_is_the_verdict(self):
        runs = [a_run(hours_ago=200, status=RunStatus.FAILED)]

        report = assess(runs, now=NOW)

        assert report.worst is not None
        assert report.worst.status is report.status

    def test_an_unhealthy_report_is_not_healthy(self):
        assert assess([a_run(hours_ago=999)], now=NOW).is_healthy is False


class TestWindow:
    def test_the_window_start_is_days_back(self):
        assert window_start(NOW, days=7) == NOW - timedelta(days=7)
