"""Enrichment commands.

``enrich run`` fetches the primary source behind each resolved story. It is the
only command that talks to the open web on a story's behalf, so its defaults
are conservative and its failures are visible.
"""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from engine.cli.container import Container, build_container
from engine.core.domain.workspace import Workspace
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.enrich import ATTEMPTS_KEY, ERROR_KEY, EnrichStage

console = Console()
app = typer.Typer(help="Fetch the primary source behind each story.")


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
    limit: int = typer.Option(None, "--limit", "-n", help="Max stories to enrich."),
    story: str = typer.Option(None, "--story", help="Enrich one story, by id."),
    attempts: int = typer.Option(
        None, "--attempts", help="Give up after this many failures per story."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Fetch and report without storing anything."
    ),
) -> None:
    """Fetch and extract the primary source for stories that lack one."""
    container = build_container()
    workspace = _require_workspace(container, slug)
    settings = container.settings

    stage = EnrichStage(
        http=container.http,
        extractor=container.extractor,
        max_stories=limit or settings.enrich_max_stories,
        max_attempts=attempts or settings.enrich_max_attempts,
        only_story=story,
    )

    runner = PipelineRunner(uow=container.unit_of_work(), clock=container.clock, settings=settings)

    try:
        ledger = runner.run(
            workspace_id=workspace.id,
            pipeline="enrich",
            stages=[stage],
            trigger="cli",
            dry_run=dry_run,
        )
    except Exception as exc:
        console.print(f"[red]Enrichment failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    with container.unit_of_work() as uow:
        records = uow.runs.list_stages(workspace.id, ledger.id)
    meta: dict[str, Any] = next((record.meta for record in records if record.stage == "enrich"), {})

    if meta.get("considered"):
        table = Table(title="Enrichment results" + (" (dry run)" if dry_run else ""))
        table.add_column("Considered", justify="right")
        table.add_column("Enriched", justify="right", style="green")
        table.add_column("Failed", justify="right", style="red")
        table.add_row(
            str(meta.get("considered", 0)),
            str(meta.get("enriched", 0)),
            str(meta.get("failed", 0)),
        )
        console.print(table)
        reasons = meta.get("failure_reasons", {})
        if reasons:
            console.print(
                "Failures: "
                + ", ".join(f"{reason} ({count})" for reason, count in sorted(reasons.items()))
            )
    elif meta.get("skip_reason"):
        console.print(f"[yellow]Nothing to enrich:[/yellow] {meta['skip_reason']}")

    console.print(
        f"[green]Run {ledger.status.value}:[/green] {ledger.id} ({ledger.duration_seconds:.2f}s)"
    )
    if dry_run:
        console.print("[dim]Dry run -- nothing was stored.[/dim]")


@app.command("status")
def status(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    limit: int = typer.Option(10, "--limit", "-n", help="Failing stories to show."),
) -> None:
    """Show how much of the story set carries a primary source."""
    container = build_container()
    workspace = _require_workspace(container, slug)

    with container.unit_of_work() as uow:
        by_status = uow.stories.count_by_status(workspace.id)
        total = uow.stories.count_for_workspace(workspace.id)
        waiting = uow.stories.list_needing_enrichment(
            workspace.id, limit=limit, max_attempts=container.settings.enrich_max_attempts
        )
        recent = uow.stories.list_recent(workspace.id, limit=200)

    enriched = by_status.get("enriched", 0)
    console.print(
        f"Stories: [cyan]{total}[/cyan] total, [green]{enriched}[/green] carry a primary source"
        + (f" ({enriched / total:.0%})" if total else "")
    )
    if waiting:
        console.print(f"[dim]{len(waiting)} awaiting enrichment (showing up to {limit})[/dim]")

    failing = [story for story in recent if story.meta.get(ERROR_KEY) and story.enrichment is None]
    if not failing:
        console.print("[green]No enrichment failures recorded.[/green]")
        return

    table = Table(title="Stories whose primary source could not be read")
    table.add_column("ID", style="dim")
    table.add_column("Headline", style="cyan", overflow="fold")
    table.add_column("Attempts", justify="right")
    table.add_column("Reason", style="red", overflow="fold")
    for story in failing[:limit]:
        table.add_row(
            story.id[-8:],
            story.canonical_headline[:50],
            str(story.meta.get(ATTEMPTS_KEY, 0)),
            str(story.meta.get(ERROR_KEY, ""))[:60],
        )
    console.print(table)
