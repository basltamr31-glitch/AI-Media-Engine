"""Ingestion commands.

``ingest run`` executes the real pipeline runner with a single stage rather than
calling the stage directly. That costs a few lines and buys the run ledger, cost
accounting, structured logging and failure recording -- and means ingestion
behaves identically whether triggered here, by the scheduler, or by the API.
"""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from engine.cli.container import Container, build_container
from engine.core.domain.source import Source, SourceDocument
from engine.core.domain.workspace import Workspace
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.ingest import IngestStage

console = Console()
app = typer.Typer(help="Fetch documents from configured sources.")


def _require_workspace(container: Container, slug: str | None) -> Workspace:
    target = slug or container.settings.default_workspace_slug
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target)
    if workspace is None:
        console.print(f"[red]Workspace {target!r} not found.[/red] Run: engine workspace seed")
        raise typer.Exit(code=1)
    return workspace


@app.command("run")
def run(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    source: str = typer.Option(None, "--source", help="Ingest only this source, by name."),
    limit: int = typer.Option(None, "--limit", "-n", help="Max documents per source."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Fetch and report without storing anything."
    ),
) -> None:
    """Fetch new documents from every active source."""
    container = build_container()
    workspace = _require_workspace(container, slug)

    stage = IngestStage(
        fetchers=container.fetchers,
        archive=container.raw_archive,
        max_documents=limit or container.settings.ingest_max_documents,
        only_source=source,
    )

    runner = PipelineRunner(
        uow=container.unit_of_work(),
        clock=container.clock,
        settings=container.settings,
    )

    try:
        ledger = runner.run(
            workspace_id=workspace.id,
            pipeline="ingest",
            stages=[stage],
            trigger="cli",
            dry_run=dry_run,
        )
    except Exception as exc:
        console.print(f"[red]Ingestion failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    with container.unit_of_work() as uow:
        records = uow.runs.list_stages(workspace.id, ledger.id)

    stats: dict[str, Any] = {}
    for record in records:
        if record.stage == "ingest":
            stats = record.meta.get("sources", {})

    if stats:
        table = Table(title="Ingestion results" + (" (dry run)" if dry_run else ""))
        table.add_column("Source", style="cyan")
        table.add_column("Kind")
        table.add_column("Fetched", justify="right")
        table.add_column("New", justify="right", style="green")
        table.add_column("Duplicate", justify="right", style="dim")
        table.add_column("Error", style="red")
        for name, values in stats.items():
            table.add_row(
                name,
                str(values.get("kind", "")),
                str(values.get("fetched", 0)),
                str(values.get("stored", 0)),
                str(values.get("duplicates", 0)),
                (values.get("error") or "")[:60],
            )
        console.print(table)

    console.print(
        f"[green]Run {ledger.status.value}:[/green] {ledger.id} ({ledger.duration_seconds:.2f}s)"
    )
    if dry_run:
        console.print("[dim]Dry run -- nothing was stored or archived.[/dim]")


@app.command("status")
def status(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
) -> None:
    """Show what has been ingested from each source."""
    container = build_container()
    workspace = _require_workspace(container, slug)

    with container.unit_of_work() as uow:
        sources = uow.sources.list_for_workspace(workspace.id, only_active=False)
        rows: list[tuple[Source, int, SourceDocument | None]] = []
        for source in sources:
            total = uow.documents.count_for_source(workspace.id, source.id)
            recent = uow.documents.list_for_source(workspace.id, source.id, limit=1)
            rows.append((source, total, recent[0] if recent else None))

    if not rows:
        console.print("[yellow]No sources configured.[/yellow] Run: engine workspace seed")
        return

    table = Table(title=f"Ingestion status for {workspace.slug}")
    table.add_column("Source", style="cyan")
    table.add_column("Kind")
    table.add_column("Active")
    table.add_column("Documents", justify="right")
    table.add_column("Last fetched (UTC)")
    table.add_column("Most recent title", style="dim")

    for source, total, latest in rows:
        table.add_row(
            source.name,
            str(source.kind),
            "yes" if source.is_active else "[dim]no[/dim]",
            str(total),
            latest.fetched_at.strftime("%Y-%m-%d %H:%M") if latest else "-",
            (latest.title or "")[:50] if latest else "-",
        )
    console.print(table)

    archive_dir = container.resolve_path(container.settings.raw_archive_dir)
    if container.settings.ingest_archive_raw:
        console.print(f"[dim]Raw payloads archived under {archive_dir}[/dim]")
