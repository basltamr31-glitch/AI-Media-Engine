"""Pipeline commands.

Phase 0 registers no stages, so ``pipeline run`` executes an empty pipeline.
That is the point: it proves the whole spine works -- container wiring, run
context, ledger persistence, stage recording, cost ceiling, transactions --
before any business logic exists to hide a defect in it.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from engine.cli.container import build_container
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.base import Stage
from engine.pipeline.stages.noop import NoOpStage

console = Console()
app = typer.Typer(help="Pipeline execution and run history.")


@app.command("run")
def run(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Run without side effects."),
    smoke: bool = typer.Option(
        False, "--smoke", help="Include a no-op stage to exercise the orchestrator."
    ),
) -> None:
    """Execute the daily content pipeline."""
    container = build_container()
    target_slug = slug or container.settings.default_workspace_slug

    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target_slug)
    if workspace is None:
        console.print(f"[red]Workspace {target_slug!r} not found.[/red] Run: engine workspace seed")
        raise typer.Exit(code=1)

    # Phase 1 replaces this with the real stage list.
    stages: list[Stage] = [NoOpStage("smoke")] if smoke else []

    runner = PipelineRunner(
        uow=container.unit_of_work(),
        clock=container.clock,
        settings=container.settings,
    )
    ledger = runner.run(
        workspace_id=workspace.id,
        pipeline="daily_content",
        stages=stages,
        trigger="cli",
        dry_run=dry_run,
    )

    console.print(
        f"[green]Run {ledger.status.value}:[/green] {ledger.id} "
        f"({len(stages)} stage(s), {ledger.duration_seconds:.3f}s, "
        f"${ledger.cost_usd:.4f})"
    )
    if not stages:
        console.print("[dim]No stages registered yet -- pipeline stages arrive in Phase 1.[/dim]")


@app.command("runs")
def list_runs(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    limit: int = typer.Option(10, "--limit", "-n", help="How many runs to show."),
) -> None:
    """Show recent pipeline runs with their cost and duration."""
    container = build_container()
    target_slug = slug or container.settings.default_workspace_slug

    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target_slug)
        if workspace is None:
            console.print(f"[red]Workspace {target_slug!r} not found.[/red]")
            raise typer.Exit(code=1)
        runs = uow.runs.list_recent(workspace.id, limit=limit)

    if not runs:
        console.print("[yellow]No runs recorded yet.[/yellow]")
        return

    table = Table(title=f"Recent runs for {workspace.slug}")
    table.add_column("Started (UTC)")
    table.add_column("Pipeline", style="cyan")
    table.add_column("Status")
    table.add_column("Duration", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("ID", style="dim")
    for entry in runs:
        duration = entry.duration_seconds
        table.add_row(
            entry.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            entry.pipeline,
            entry.status.value,
            f"{duration:.2f}s" if duration is not None else "-",
            f"${entry.cost_usd:.4f}",
            entry.id,
        )
    console.print(table)


@app.command("stages")
def show_stages(
    run_id: str = typer.Argument(..., help="Run identifier."),
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
) -> None:
    """Show the per-stage breakdown of one run."""
    container = build_container()
    target_slug = slug or container.settings.default_workspace_slug

    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target_slug)
        if workspace is None:
            console.print(f"[red]Workspace {target_slug!r} not found.[/red]")
            raise typer.Exit(code=1)
        stages = uow.runs.list_stages(workspace.id, run_id)

    if not stages:
        console.print(f"[yellow]No stages recorded for run {run_id}.[/yellow]")
        return

    table = Table(title=f"Stages for {run_id}")
    table.add_column("#", justify="right")
    table.add_column("Stage", style="cyan")
    table.add_column("Status")
    table.add_column("In", justify="right")
    table.add_column("Out", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Cost", justify="right")
    for record in stages:
        duration = record.duration_seconds
        table.add_row(
            str(record.sequence),
            record.stage,
            record.status.value,
            str(record.items_in),
            str(record.items_out),
            f"{duration:.3f}s" if duration is not None else "-",
            f"${record.cost_usd:.4f}",
        )
    console.print(table)
