"""Story resolution and inspection.

``resolve run`` clusters mentions into canonical stories; ``stories list`` and
``stories show`` are how a human checks that the clustering was right, which is
the only way anyone will trust it.
"""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from engine.cli.container import Container, build_container
from engine.core.domain.workspace import Workspace
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.resolve import ResolveStage

console = Console()
resolve_app = typer.Typer(help="Cluster story candidates into canonical stories.")
stories_app = typer.Typer(help="Inspect canonical stories.")


def _require_workspace(container: Container, slug: str | None) -> Workspace:
    target = slug or container.settings.default_workspace_slug
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target)
    if workspace is None:
        console.print(f"[red]Workspace {target!r} not found.[/red] Run: engine workspace seed")
        raise typer.Exit(code=1)
    return workspace


@resolve_app.command("run")
def run(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    limit: int = typer.Option(None, "--limit", "-n", help="Max candidates to consider."),
    window: int = typer.Option(None, "--window", help="Days of stories to match against."),
    similarity: float = typer.Option(
        None, "--similarity", help="Headline overlap needed to merge (0-1). Lower merges more."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Cluster and report without storing anything."
    ),
) -> None:
    """Cluster unresolved candidates into canonical stories."""
    container = build_container()
    workspace = _require_workspace(container, slug)
    settings = container.settings

    stage = ResolveStage(
        max_candidates=limit or settings.resolve_max_candidates,
        window_days=window or settings.resolve_window_days,
        similarity_threshold=(
            similarity if similarity is not None else settings.resolve_similarity_threshold
        ),
    )

    runner = PipelineRunner(uow=container.unit_of_work(), clock=container.clock, settings=settings)

    try:
        ledger = runner.run(
            workspace_id=workspace.id,
            pipeline="resolve",
            stages=[stage],
            trigger="cli",
            dry_run=dry_run,
        )
    except Exception as exc:
        console.print(f"[red]Resolution failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    with container.unit_of_work() as uow:
        records = uow.runs.list_stages(workspace.id, ledger.id)
    meta: dict[str, Any] = next(
        (record.meta for record in records if record.stage == "resolve"), {}
    )

    if meta.get("candidates"):
        table = Table(title="Resolution results" + (" (dry run)" if dry_run else ""))
        table.add_column("Candidates", justify="right")
        table.add_column("Stories created", justify="right", style="green")
        table.add_column("Stories updated", justify="right")
        table.add_column("Multi-mention", justify="right", style="cyan")
        matched = meta.get("matched_by", {})
        table.add_row(
            str(meta.get("candidates", 0)),
            str(meta.get("stories_created", 0)),
            str(meta.get("stories_updated", 0)),
            str(meta.get("multi_mention_stories", 0)),
        )
        console.print(table)
        console.print(
            "Matched by: "
            + ", ".join(f"{name}={count}" for name, count in sorted(matched.items()))
            + f"  [dim](similarity threshold {meta.get('similarity_threshold')})[/dim]"
        )
    elif meta.get("skip_reason"):
        console.print(f"[yellow]Nothing to resolve:[/yellow] {meta['skip_reason']}")

    console.print(
        f"[green]Run {ledger.status.value}:[/green] {ledger.id} ({ledger.duration_seconds:.2f}s)"
    )
    if dry_run:
        console.print("[dim]Dry run -- nothing was stored.[/dim]")


@stories_app.command("list")
def list_stories(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    limit: int = typer.Option(20, "--limit", "-n", help="How many stories to show."),
    corroborated: bool = typer.Option(
        False, "--corroborated", help="Only stories reported by more than one source."
    ),
) -> None:
    """List canonical stories, most recently seen first."""
    container = build_container()
    workspace = _require_workspace(container, slug)

    with container.unit_of_work() as uow:
        stories = (
            uow.stories.list_corroborated(workspace.id, limit=limit)
            if corroborated
            else uow.stories.list_recent(workspace.id, limit=limit)
        )
        total = uow.stories.count_for_workspace(workspace.id)
        by_status = uow.stories.count_by_status(workspace.id)
        sources = {
            source.id: source.name
            for source in uow.sources.list_for_workspace(workspace.id, only_active=False)
        }

    if not stories:
        if corroborated:
            console.print("[yellow]No story has been reported by more than one source.[/yellow]")
        else:
            console.print("[yellow]No stories yet.[/yellow] Run: engine resolve run")
        return

    table = Table(title=f"Stories for {workspace.slug}")
    table.add_column("ID", style="dim")
    table.add_column("Headline", style="cyan", overflow="fold")
    table.add_column("Mentions", justify="right")
    table.add_column("Sources", justify="right", style="green")
    table.add_column("Status")
    table.add_column("Last seen (UTC)")
    for story in stories:
        table.add_row(
            story.id[-8:],
            story.canonical_headline[:70],
            str(len(story.candidate_ids)),
            str(story.source_count),
            story.status.value,
            story.last_seen_at.strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)
    console.print(
        f"Total: [cyan]{total}[/cyan] "
        + ", ".join(f"{status}={count}" for status, count in sorted(by_status.items()))
    )
    if not corroborated:
        console.print(f"[dim]Sources configured: {', '.join(sorted(sources.values()))}[/dim]")


@stories_app.command("reset")
def reset_stories(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Discard every story so candidates can be clustered again.

    Needed to re-tune ``--similarity``: once mentions are attached, a changed
    threshold has nothing left to act on. The mentions themselves are never
    deleted -- they return to the unresolved pool.
    """
    container = build_container()
    workspace = _require_workspace(container, slug)

    with container.unit_of_work() as uow:
        total = uow.stories.count_for_workspace(workspace.id)
        enriched = uow.stories.count_by_status(workspace.id).get("enriched", 0)

    if not total:
        console.print("[yellow]No stories to discard.[/yellow]")
        return

    # Enrichment lives on the story, so discarding stories discards it -- and
    # it is the only thing here that cost hundreds of requests to somebody
    # else's server. Cheap to say, expensive to discover afterwards.
    if enriched:
        console.print(
            f"[yellow]Warning:[/yellow] {enriched} story(ies) carry fetched primary-source "
            "content, which is stored on the story and will be discarded with it. "
            "Re-running enrichment afterwards re-fetches every page."
        )
    if not yes:
        typer.confirm(
            f"Discard {total} story(ies) in {workspace.slug} and re-cluster from scratch?",
            abort=True,
        )

    with container.unit_of_work() as uow:
        deleted = uow.stories.delete_all(workspace.id)
        uow.commit()

    console.print(
        f"[green]Discarded {deleted} story(ies).[/green] "
        "Mentions are unchanged -- run: engine resolve run"
    )


@stories_app.command("show")
def show_story(
    story_id: str = typer.Argument(..., help="Story id, or its last 8 characters."),
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
) -> None:
    """Show one story and every mention it was assembled from."""
    container = build_container()
    workspace = _require_workspace(container, slug)

    with container.unit_of_work() as uow:
        story = uow.stories.get(workspace.id, story_id)
        if story is None:
            # Accept the short form printed by `stories list`.
            matches = [
                candidate
                for candidate in uow.stories.list_recent(workspace.id, limit=1000)
                if candidate.id.endswith(story_id)
            ]
            story = matches[0] if len(matches) == 1 else None
        if story is None:
            console.print(f"[red]Story {story_id!r} not found.[/red]")
            raise typer.Exit(code=1)
        mentions = uow.candidates.list_for_story(workspace.id, story.id)
        evidence = uow.evidence.list_for_story(workspace.id, story.id)
        sources = {
            source.id: source
            for source in uow.sources.list_for_workspace(workspace.id, only_active=False)
        }

    console.print(f"[bold cyan]{story.canonical_headline}[/bold cyan]")
    console.print(f"[dim]{story.id}  {story.status.value}[/dim]")
    if story.primary_url:
        console.print(f"Primary source: {story.primary_url}")
    if story.canonical_summary:
        console.print(f"\n{story.canonical_summary}\n")
    console.print(
        f"Reported by [green]{story.source_count}[/green] source(s) "
        f"in {len(story.candidate_ids)} mention(s), "
        f"first seen {story.first_seen_at:%Y-%m-%d %H:%M} UTC"
    )
    if evidence:
        kinds: dict[str, int] = {}
        for record in evidence:
            kinds[record.kind.value] = kinds.get(record.kind.value, 0) + 1
        console.print(
            "Evidence: "
            + ", ".join(f"{kind}={count}" for kind, count in sorted(kinds.items()))
            + f"  [dim](engine evidence show {story.id[-8:]})[/dim]"
        )
    else:
        console.print("[yellow]No evidence recorded.[/yellow] Run: engine evidence run")

    table = Table(title="Mentions")
    table.add_column("Source", style="cyan")
    table.add_column("Trust", justify="right")
    table.add_column("Headline", overflow="fold")
    table.add_column("Link", style="dim", overflow="fold")
    for mention in mentions:
        source = sources.get(mention.source_id)
        table.add_row(
            source.name if source else mention.source_id,
            f"{source.trust_weight:.2f}" if source else "-",
            mention.headline[:70],
            (mention.primary_url or "-")[:60],
        )
    console.print(table)
