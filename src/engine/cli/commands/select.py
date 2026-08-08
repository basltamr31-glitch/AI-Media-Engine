"""Selection commands.

``select run`` decides what gets produced; ``select list`` shows what was
chosen. The interesting output is the *refusals* — a run that selects nothing
must say which gate did it, because "no stories today" and "the pipeline is
broken" call for opposite responses.
"""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from engine.adapters.config.policy_loader import PolicyLoadError
from engine.cli.container import Container, build_container
from engine.core.domain.policy import EditorialPolicy
from engine.core.domain.workspace import Workspace
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.select import RANK_KEY, SCORE_KEY, SelectStage

console = Console()
app = typer.Typer(help="Choose which stories are worth producing.")

#: Plain-English gate names. The stage records stable keys; the operator should
#: not have to translate them.
_REASONS = {
    "below_minimum_score": "scored below the minimum",
    "no_primary_source": "no primary source read first-hand",
    "not_scored": "not scored yet",
    "quota_reached": "good enough, but the quota was full",
}


def _require_workspace(container: Container, slug: str | None) -> Workspace:
    target = slug or container.settings.default_workspace_slug
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target)
    if workspace is None:
        console.print(f"[red]Workspace {target!r} not found.[/red] Run: engine workspace seed")
        raise typer.Exit(code=1)
    return workspace


def _policy(container: Container, workspace: Workspace) -> EditorialPolicy:
    try:
        return container.policy_loader.load_workspace(workspace.slug).editorial
    except PolicyLoadError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@app.command("run")
def run(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    limit: int = typer.Option(
        None, "--limit", "-n", help="Override the policy's stories-per-run quota."
    ),
    window: int = typer.Option(None, "--window", help="Hours the quota is counted over."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Decide and report without storing anything."
    ),
) -> None:
    """Select the stories worth producing."""
    container = build_container()
    workspace = _require_workspace(container, slug)
    policy = _policy(container, workspace)
    settings = container.settings

    stage = SelectStage(
        policy=policy,
        max_stories=limit,
        window_hours=window or settings.select_window_hours,
        candidate_window_days=settings.resolve_window_days,
    )
    runner = PipelineRunner(uow=container.unit_of_work(), clock=container.clock, settings=settings)

    try:
        ledger = runner.run(
            workspace_id=workspace.id,
            pipeline="select",
            stages=[stage],
            trigger="cli",
            dry_run=dry_run,
        )
    except Exception as exc:
        console.print(f"[red]Selection failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    with container.unit_of_work() as uow:
        records = uow.runs.list_stages(workspace.id, ledger.id)
    meta: dict[str, Any] = next((record.meta for record in records if record.stage == "select"), {})

    if not meta or meta.get("skip_reason"):
        console.print(
            f"[yellow]Nothing to select:[/yellow] {meta.get('skip_reason', 'no stories')}"
        )
    else:
        _print_outcome(meta, dry_run=dry_run)

    console.print(
        f"[green]Run {ledger.status.value}:[/green] {ledger.id} ({ledger.duration_seconds:.2f}s)"
    )
    if dry_run:
        console.print("[dim]Dry run -- nothing was stored.[/dim]")


def _print_outcome(meta: dict[str, Any], *, dry_run: bool) -> None:
    chosen = meta.get("chosen", [])
    if chosen:
        table = Table(title="Selected" + (" (dry run)" if dry_run else ""))
        table.add_column("#", justify="right", style="dim")
        table.add_column("Score", justify="right", style="green")
        table.add_column("Headline", style="cyan", overflow="fold")
        for position, entry in enumerate(chosen, start=1):
            table.add_row(str(position), f"{entry['score']:.4f}", entry["headline"][:70])
        console.print(table)
    else:
        console.print("[yellow]Nothing selected.[/yellow]")

    already = meta.get("already_selected_in_window", 0)
    console.print(
        f"Quota {meta.get('quota')} per {meta.get('window_hours')}h"
        + (f", {already} already selected in that window" if already else "")
        + f"  [dim](minimum score {meta.get('minimum_score')}, "
        + ("primary source required" if meta.get("require_primary_source") else "no source gate")
        + ")[/dim]"
    )

    refused = meta.get("refused", {})
    if not refused:
        return
    table = Table(title="Not selected")
    table.add_column("Reason")
    table.add_column("Stories", justify="right")
    for reason, count in sorted(refused.items(), key=lambda item: -item[1]):
        table.add_row(_REASONS.get(reason, reason), str(count))
    console.print(table)

    if refused.get("no_primary_source"):
        console.print("[dim]Those need: engine enrich run[/dim]")
    if refused.get("not_scored"):
        console.print("[dim]Those need: engine score run[/dim]")


@app.command("list")
def list_selected(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    limit: int = typer.Option(20, "--limit", "-n", help="How many to show."),
) -> None:
    """Show the stories editorial has chosen."""
    container = build_container()
    workspace = _require_workspace(container, slug)

    with container.unit_of_work() as uow:
        stories = uow.stories.list_selected(workspace.id, limit=limit)

    if not stories:
        console.print("[yellow]Nothing selected yet.[/yellow] Run: engine select run")
        return

    table = Table(title=f"Selected stories for {workspace.slug}")
    table.add_column("Selected (UTC)")
    table.add_column("Rank", justify="right", style="dim")
    table.add_column("Score", justify="right", style="green")
    table.add_column("Headline", style="cyan", overflow="fold")
    table.add_column("Primary source", style="dim", overflow="fold")
    for story in stories:
        table.add_row(
            story.selected_at.strftime("%Y-%m-%d %H:%M") if story.selected_at else "-",
            str(story.meta.get(RANK_KEY, "-")),
            f"{float(story.meta.get(SCORE_KEY, 0)):.4f}",
            story.canonical_headline[:60],
            (story.primary_url or "-")[:50],
        )
    console.print(table)
