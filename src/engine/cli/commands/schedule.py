"""Scheduling: the daily pipeline, run by a machine instead of a person.

There is deliberately **no long-lived daemon here**. The engine exposes one
idempotent command that does a day's work and exits; Task Scheduler, cron,
systemd timers and every CI runner already know how to call that reliably,
restart it after a reboot, and log its output. A bespoke scheduler process
would reimplement all of it, worse, and add a thing that can itself fall over
silently at 3am.

What the engine does own is the part a generic scheduler cannot do for it:

* **Not running twice at once.** A pipeline that overruns its interval would
  otherwise be started again on top of itself.
* **Reporting failure as a failure.** Exit codes are what a scheduler
  understands, so a degraded run has to become one.
* **Saying what it did.** ``engine schedule status`` answers "is this actually
  running, and has it been working?" without reading a log file.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from engine.adapters.alerts import alert_from_failure
from engine.adapters.locking import LockHeldError, RunLock
from engine.cli.commands.pipeline import build_daily_stages
from engine.cli.container import Container, build_container
from engine.core.domain.enums import RunStatus
from engine.core.domain.health import (
    DEFAULT_MAX_AGE_HOURS,
    HealthReport,
    HealthStatus,
    assess,
    window_start,
)
from engine.core.domain.ledger import RunLedger
from engine.core.domain.workspace import Workspace
from engine.observability.logging import get_logger
from engine.pipeline.runner import PipelineRunner

console = Console()
logger = get_logger(__name__)
app = typer.Typer(help="Run the pipeline unattended, and check that it is working.")

#: Exit codes a scheduler can branch on. Distinct values because "already
#: running" is normal and "the run failed" is not, and a scheduler that cannot
#: tell them apart either alerts on healthy skips or stays silent on real ones.
EXIT_FAILED = 1
EXIT_ALREADY_RUNNING = 2

LOCK_NAME = "daily.lock"


def _require_workspace(container: Container, slug: str | None) -> Workspace:
    target = slug or container.settings.default_workspace_slug
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target)
    if workspace is None:
        console.print(f"[red]Workspace {target!r} not found.[/red] Run: engine workspace seed")
        raise typer.Exit(code=EXIT_FAILED)
    return workspace


def _lock_path(container: Container, workspace: Workspace) -> Path:
    """One lock per workspace: two tenants' runs do not conflict."""
    return container.resolve_path(container.settings.data_dir) / f"{workspace.slug}.{LOCK_NAME}"


@app.command("run")
def run(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Run without side effects."),
    force: bool = typer.Option(
        False, "--force", help="Run even if another run holds the lock. Rarely what you want."
    ),
) -> None:
    """Execute the daily pipeline unattended.

    This is the command to put in a scheduler. It is idempotent: every stage
    skips what it has already done, so running it more often than there is news
    costs almost nothing and running it twice does not duplicate anything.
    """
    container = build_container()
    workspace = _require_workspace(container, slug)
    lock = RunLock(_lock_path(container, workspace))

    if not force:
        try:
            lock.acquire()
        except LockHeldError as exc:
            # Not an error: the previous run is simply still going. Reported on
            # stdout with its own exit code so a scheduler can stay quiet about
            # it while still alerting on genuine failures.
            console.print(f"[yellow]Skipped:[/yellow] {exc}")
            raise typer.Exit(code=EXIT_ALREADY_RUNNING) from exc

    try:
        ledger = _execute(container, workspace, dry_run=dry_run)
    except Exception as exc:
        # The pipeline runner records a failed ledger for anything a stage
        # raises, so reaching here means the failure was outside it -- the
        # database, the container, the config. Nobody is watching, so it has to
        # be reported before it is re-raised.
        container.alerts.send(
            alert_from_failure(
                code="run_crashed",
                summary=f"Scheduled run crashed before finishing: {type(exc).__name__}",
                detail=str(exc),
                workspace_slug=workspace.slug,
            )
        )
        raise
    finally:
        lock.release()

    _report(ledger)
    if ledger.status is not RunStatus.SUCCEEDED:
        container.alerts.send(
            alert_from_failure(
                code="run_failed",
                summary=f"Scheduled run {ledger.status.value} for {workspace.slug}",
                detail=ledger.error or "no reason recorded",
                workspace_slug=workspace.slug,
                run_id=ledger.id,
            )
        )
        raise typer.Exit(code=EXIT_FAILED)


def _execute(container: Container, workspace: Workspace, *, dry_run: bool) -> RunLedger:
    """Run the pipeline, recording that a schedule asked for it."""
    stages = build_daily_stages(container, workspace)
    runner = PipelineRunner(
        uow=container.unit_of_work(),
        clock=container.clock,
        settings=container.settings,
    )
    return runner.run(
        workspace_id=workspace.id,
        pipeline="daily_content",
        stages=stages,
        # The one thing that distinguishes this from `pipeline run`. Unattended
        # runs have different failure patterns from supervised ones, and they
        # cannot be told apart afterwards unless it is recorded now.
        trigger="schedule",
        dry_run=dry_run,
    )


def _report(ledger: RunLedger) -> None:
    colour = "green" if ledger.status is RunStatus.SUCCEEDED else "red"
    duration = ledger.duration_seconds or 0.0
    console.print(
        f"[{colour}]Run {ledger.status.value}:[/{colour}] {ledger.id} "
        f"({duration:.2f}s, ${ledger.cost_usd:.4f})"
    )
    if ledger.error:
        console.print(f"[red]{ledger.error}[/red]")
    console.print("[dim]Then review what it wrote: engine review queue[/dim]")


@app.command("status")
def status(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    window: int = typer.Option(7, "--days", help="How many days of history to summarise."),
) -> None:
    """Show whether scheduled runs are happening, and whether they work.

    The question this answers is the one that matters for unattended
    operation and that no single run can answer: not "did the last run pass?"
    but "has it been running at all?" A scheduler that stopped firing produces
    no failures, which is why silence has to be reported as a state.
    """
    container = build_container()
    workspace = _require_workspace(container, slug)
    lock = RunLock(_lock_path(container, workspace))

    since = datetime.now(UTC) - timedelta(days=window)
    with container.unit_of_work() as uow:
        recent = [
            entry
            for entry in uow.runs.list_recent(workspace.id, limit=200)
            if entry.started_at >= since
        ]

    scheduled = [entry for entry in recent if entry.trigger == "schedule"]
    failures = [entry for entry in scheduled if entry.status is not RunStatus.SUCCEEDED]

    owner = lock.read_owner()
    if owner is not None:
        console.print(
            f"[cyan]A run is in progress[/cyan] (pid {owner.pid}, "
            f"started {owner.acquired_at:%Y-%m-%d %H:%M:%S}Z)"
        )

    if not scheduled:
        console.print(
            f"[yellow]No scheduled runs in the last {window} days.[/yellow] "
            "If that is unexpected, the scheduler is not firing -- a stopped "
            "scheduler reports no failures."
        )
        console.print("[dim]Set one up: see docs/OPERATIONS.md[/dim]")
        return

    last = scheduled[0]
    age = datetime.now(UTC) - last.started_at
    console.print(
        f"Last scheduled run {last.status.value} {_humanise(age)} ago  [dim]({last.id})[/dim]"
    )

    table = Table(title=f"Scheduled runs, last {window} days")
    table.add_column("Started (UTC)")
    table.add_column("Status")
    table.add_column("Duration", justify="right")
    table.add_column("Cost", justify="right")
    for entry in scheduled[:10]:
        duration = entry.duration_seconds
        style = "green" if entry.status is RunStatus.SUCCEEDED else "red"
        table.add_row(
            entry.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            f"[{style}]{entry.status.value}[/{style}]",
            f"{duration:.2f}s" if duration is not None else "-",
            f"${entry.cost_usd:.4f}",
        )
    console.print(table)

    console.print(
        f"{len(scheduled)} run(s), {len(failures)} failed, "
        f"${sum(entry.cost_usd for entry in scheduled):.4f} total"
    )


def _humanise(delta: timedelta) -> str:
    """A duration an operator can read at a glance."""
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86_400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86_400}d"


@app.command("health")
def health(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    days: int = typer.Option(7, "--days", help="Days of history to judge."),
    max_age: float = typer.Option(
        DEFAULT_MAX_AGE_HOURS, "--max-age-hours", help="Hours without a run before it is stale."
    ),
    alert: bool = typer.Option(
        False, "--alert", help="Raise an alert if unhealthy, as well as reporting."
    ),
) -> None:
    """Report whether unattended running is actually working.

    Exits non-zero when it is not, so a monitor can call this on its own timer
    and learn about the failure that produces no failed runs: a scheduler that
    has stopped firing.
    """
    container = build_container()
    workspace = _require_workspace(container, slug)

    now = datetime.now(UTC)
    since = window_start(now, days=days)
    with container.unit_of_work() as uow:
        scheduled = [
            entry
            for entry in uow.runs.list_recent(workspace.id, limit=500)
            if entry.trigger == "schedule" and entry.started_at >= since
        ]

    report = assess(scheduled, now=now, max_age_hours=max_age)
    _print_health(report)

    if alert and not report.is_healthy:
        worst = report.worst
        container.alerts.send(
            alert_from_failure(
                code=f"health_{worst.code}" if worst else "health_unhealthy",
                summary=f"{workspace.slug} is {report.status.value}",
                detail=worst.summary if worst else "",
                workspace_slug=workspace.slug,
                critical=report.status in {HealthStatus.STALE, HealthStatus.FAILING},
            )
        )

    if not report.is_healthy:
        raise typer.Exit(code=EXIT_FAILED)


def _print_health(report: HealthReport) -> None:
    colour = {
        HealthStatus.HEALTHY: "green",
        HealthStatus.UNKNOWN: "yellow",
        HealthStatus.DEGRADED: "yellow",
        HealthStatus.FAILING: "red",
        HealthStatus.STALE: "red",
    }[report.status]
    console.print(f"[{colour}]{report.status.value.upper()}[/{colour}]")
    for finding in report.findings:
        console.print(f"  - {finding.summary}")
    if report.runs_considered:
        console.print(
            f"[dim]{report.runs_considered} scheduled run(s), {report.failures} failed[/dim]"
        )
