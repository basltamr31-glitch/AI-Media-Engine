"""Evidence commands.

``evidence run`` reconciles the provenance ledger; ``evidence show`` prints
what supports one story. The second is the point of the first: provenance
nobody can read is provenance nobody trusts.
"""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from engine.cli.container import Container, build_container
from engine.core.domain.enums import EvidenceKind
from engine.core.domain.workspace import Workspace
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.evidence import EvidenceStage

console = Console()
app = typer.Typer(help="The provenance ledger: what supports each story.")

#: Strongest first, so a story's best evidence is the first thing read.
_KIND_ORDER = {
    EvidenceKind.PRIMARY: 0,
    EvidenceKind.SECONDARY: 1,
    EvidenceKind.AGGREGATOR: 2,
}
_KIND_STYLE = {
    EvidenceKind.PRIMARY: "green",
    EvidenceKind.SECONDARY: "cyan",
    EvidenceKind.AGGREGATOR: "yellow",
}


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
    limit: int = typer.Option(None, "--limit", "-n", help="Max stories to reconcile."),
    window: int = typer.Option(None, "--window", help="Days of stories to cover."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Reconcile and report without storing anything."
    ),
) -> None:
    """Record what supports each recent story."""
    container = build_container()
    workspace = _require_workspace(container, slug)
    settings = container.settings

    stage = EvidenceStage(
        max_stories=limit or settings.evidence_max_stories,
        window_days=window or settings.resolve_window_days,
    )
    runner = PipelineRunner(uow=container.unit_of_work(), clock=container.clock, settings=settings)

    try:
        ledger = runner.run(
            workspace_id=workspace.id,
            pipeline="evidence",
            stages=[stage],
            trigger="cli",
            dry_run=dry_run,
        )
    except Exception as exc:
        console.print(f"[red]Evidence reconciliation failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    with container.unit_of_work() as uow:
        records = uow.runs.list_stages(workspace.id, ledger.id)
        by_kind = uow.evidence.count_by_kind(workspace.id)
        total = uow.evidence.count_for_workspace(workspace.id)
    meta: dict[str, Any] = next(
        (record.meta for record in records if record.stage == "evidence"), {}
    )

    if meta.get("stories"):
        table = Table(title="Evidence" + (" (dry run)" if dry_run else ""))
        table.add_column("Stories", justify="right")
        table.add_column("Recorded", justify="right", style="green")
        table.add_column("Aggregator-only stories", justify="right", style="yellow")
        table.add_row(
            str(meta.get("stories", 0)),
            str(meta.get("recorded", 0)),
            str(meta.get("aggregator_only_stories", 0)),
        )
        console.print(table)
        if not dry_run and by_kind:
            console.print(
                f"Ledger: [cyan]{total}[/cyan] records -- "
                + ", ".join(f"{kind}={count}" for kind, count in sorted(by_kind.items()))
            )
        if meta.get("aggregator_only_stories"):
            console.print(
                "[yellow]Stories supported only by aggregators cannot be written about[/yellow]"
                " -- run: engine enrich run"
            )
    elif meta.get("skip_reason"):
        console.print(f"[yellow]Nothing to reconcile:[/yellow] {meta['skip_reason']}")

    console.print(
        f"[green]Run {ledger.status.value}:[/green] {ledger.id} ({ledger.duration_seconds:.2f}s)"
    )


@app.command("show")
def show(
    story_id: str = typer.Argument(..., help="Story id, or its last 8 characters."),
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
) -> None:
    """Show every source supporting one story."""
    container = build_container()
    workspace = _require_workspace(container, slug)

    with container.unit_of_work() as uow:
        story = uow.stories.get(workspace.id, story_id)
        if story is None:
            matches = [
                candidate
                for candidate in uow.stories.list_recent(workspace.id, limit=1000)
                if candidate.id.endswith(story_id)
            ]
            story = matches[0] if len(matches) == 1 else None
        if story is None:
            console.print(f"[red]Story {story_id!r} not found.[/red]")
            raise typer.Exit(code=1)
        records = uow.evidence.list_for_story(workspace.id, story.id)

    console.print(f"[bold cyan]{story.canonical_headline}[/bold cyan]")
    if not records:
        console.print("[yellow]No evidence recorded.[/yellow] Run: engine evidence run")
        return

    table = Table(title="Evidence")
    table.add_column("Kind")
    table.add_column("Source", style="cyan", overflow="fold")
    table.add_column("URL", style="dim", overflow="fold")
    table.add_column("Captured (UTC)")
    for record in sorted(records, key=lambda item: _KIND_ORDER.get(item.kind, 9)):
        style = _KIND_STYLE.get(record.kind, "white")
        table.add_row(
            f"[{style}]{record.kind.value}[/{style}]",
            (record.source_title or "-")[:40],
            record.source_url[:70],
            record.captured_at.strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)

    if all(record.kind is EvidenceKind.AGGREGATOR for record in records):
        console.print(
            "[yellow]Only aggregator evidence:[/yellow] this story has not been read first-hand."
        )
