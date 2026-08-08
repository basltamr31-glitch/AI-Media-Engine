"""Parsing commands.

Like ``ingest run``, ``parse run`` goes through the real pipeline runner rather
than calling the stage directly: the run ledger, cost accounting and failure
recording come for free, and parsing behaves identically whether triggered
here, by the scheduler, or later by the API.
"""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from engine.cli.container import Container, build_container
from engine.core.domain.workspace import Workspace
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.parse import ParseStage

console = Console()
app = typer.Typer(help="Extract story candidates from ingested documents.")


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
    source: str = typer.Option(None, "--source", help="Parse only this source, by name."),
    limit: int = typer.Option(None, "--limit", "-n", help="Max documents to consider."),
    reparse: bool = typer.Option(
        False, "--reparse", help="Re-extract documents that already have candidates."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Parse and report without storing anything."
    ),
) -> None:
    """Extract story candidates from documents that have none."""
    container = build_container()
    workspace = _require_workspace(container, slug)

    stage = ParseStage(
        parsers=container.parsers,
        max_documents=limit or container.settings.parse_max_documents,
        only_source=source,
        reparse=reparse,
    )

    runner = PipelineRunner(
        uow=container.unit_of_work(),
        clock=container.clock,
        settings=container.settings,
    )

    try:
        ledger = runner.run(
            workspace_id=workspace.id,
            pipeline="parse",
            stages=[stage],
            trigger="cli",
            dry_run=dry_run,
        )
    except Exception as exc:
        console.print(f"[red]Parsing failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    with container.unit_of_work() as uow:
        records = uow.runs.list_stages(workspace.id, ledger.id)

    stats: dict[str, Any] = {}
    skipped_reason = None
    for record in records:
        if record.stage == "parse":
            stats = record.meta.get("sources", {})
            skipped_reason = record.meta.get("skip_reason")

    if stats:
        table = Table(title="Parsing results" + (" (dry run)" if dry_run else ""))
        table.add_column("Source", style="cyan")
        table.add_column("Parser")
        table.add_column("Documents", justify="right")
        table.add_column("Parsed", justify="right", style="green")
        table.add_column("Candidates", justify="right", style="green")
        table.add_column("Already parsed", justify="right", style="dim")
        table.add_column("Failed", justify="right", style="red")
        table.add_column("Error", style="red")
        for name, values in stats.items():
            table.add_row(
                name,
                str(values.get("parser", "")),
                str(values.get("documents", 0)),
                str(values.get("parsed", 0)),
                str(values.get("candidates", 0)),
                str(values.get("already_parsed", 0)),
                str(values.get("failed", 0)),
                (values.get("error") or "")[:50],
            )
        console.print(table)
    elif skipped_reason:
        console.print(f"[yellow]Nothing to parse:[/yellow] {skipped_reason}")

    console.print(
        f"[green]Run {ledger.status.value}:[/green] {ledger.id} ({ledger.duration_seconds:.2f}s)"
    )
    if dry_run:
        console.print("[dim]Dry run -- nothing was stored.[/dim]")


@app.command("status")
def status(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    limit: int = typer.Option(10, "--limit", "-n", help="Recent candidates to show."),
) -> None:
    """Show what has been extracted, per source."""
    container = build_container()
    workspace = _require_workspace(container, slug)

    with container.unit_of_work() as uow:
        sources = uow.sources.list_for_workspace(workspace.id, only_active=False)
        per_source = uow.candidates.count_by_source(workspace.id)
        total = uow.candidates.count_for_workspace(workspace.id)
        recent = uow.candidates.list_for_workspace(workspace.id, limit=limit)

    table = Table(title=f"Extraction status for {workspace.slug}")
    table.add_column("Source", style="cyan")
    table.add_column("Parser")
    table.add_column("Documents", justify="right")
    table.add_column("Candidates", justify="right")
    for source in sources:
        with container.unit_of_work() as uow:
            documents = uow.documents.count_for_source(workspace.id, source.id)
        table.add_row(
            source.name,
            source.parser_key or "[dim]none[/dim]",
            str(documents),
            str(per_source.get(source.id, 0)),
        )
    console.print(table)
    console.print(f"Total candidates: [cyan]{total}[/cyan]")

    if not recent:
        console.print("[yellow]No candidates yet.[/yellow] Run: engine parse run")
        return

    latest = Table(title="Most recent candidates")
    latest.add_column("#", justify="right", style="dim")
    latest.add_column("Headline", style="cyan", overflow="fold")
    latest.add_column("Section", style="dim")
    latest.add_column("Primary URL", style="dim", overflow="fold")
    for candidate in recent:
        latest.add_row(
            str(candidate.ordinal),
            candidate.headline[:70],
            str(candidate.meta.get("section", ""))[:22],
            (candidate.primary_url or "-")[:60],
        )
    console.print(latest)
