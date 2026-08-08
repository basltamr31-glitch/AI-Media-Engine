"""Scoring commands.

``score run`` ranks stories; ``score show`` explains one ranking. The second is
the point of the first — a score nobody can interrogate is a score nobody will
trust, least of all when it puts the wrong story on top.
"""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from engine.adapters.config.policy_loader import PolicyLoadError
from engine.cli.container import Container, build_container
from engine.core.domain.pack import DomainPack
from engine.core.domain.policy import EditorialPolicy
from engine.core.domain.workspace import Workspace
from engine.core.ports.packs import PackLoadError, PackNotFoundError
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.score import ScoreStage

console = Console()
app = typer.Typer(help="Rank stories, and explain the ranking.")


def _require_workspace(container: Container, slug: str | None) -> Workspace:
    target = slug or container.settings.default_workspace_slug
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target)
    if workspace is None:
        console.print(f"[red]Workspace {target!r} not found.[/red] Run: engine workspace seed")
        raise typer.Exit(code=1)
    return workspace


def _pack_and_policy(
    container: Container, workspace: Workspace
) -> tuple[DomainPack, EditorialPolicy]:
    """The pack that defines the signals and the policy that weights them."""
    try:
        config = container.policy_loader.load_workspace(workspace.slug)
        pack = container.packs.load(config.pack)
    except (PolicyLoadError, PackNotFoundError, PackLoadError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    return pack, config.editorial


@app.command("run")
def run(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    limit: int = typer.Option(None, "--limit", "-n", help="Max stories to score."),
    window: int = typer.Option(None, "--window", help="Days of stories to score."),
    rescore: bool = typer.Option(
        False, "--rescore", help="Score stories that already carry a score."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Score and report without storing anything."
    ),
) -> None:
    """Score recent stories against the workspace's scoring profile."""
    container = build_container()
    workspace = _require_workspace(container, slug)
    pack, policy = _pack_and_policy(container, workspace)
    settings = container.settings

    stage = ScoreStage(
        computers=container.signal_computers,
        pack=pack,
        policy=policy,
        max_stories=limit or settings.score_max_stories,
        window_days=window or settings.resolve_window_days,
        rescore=rescore,
    )
    runner = PipelineRunner(uow=container.unit_of_work(), clock=container.clock, settings=settings)

    try:
        ledger = runner.run(
            workspace_id=workspace.id,
            pipeline="score",
            stages=[stage],
            trigger="cli",
            dry_run=dry_run,
        )
    except Exception as exc:
        console.print(f"[red]Scoring failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    with container.unit_of_work() as uow:
        records = uow.runs.list_stages(workspace.id, ledger.id)
        top = uow.scores.top_for_run(workspace.id, ledger.id, limit=10)
    meta: dict[str, Any] = next((record.meta for record in records if record.stage == "score"), {})

    if meta.get("scored"):
        console.print(
            f"Scored [green]{meta['scored']}[/green] story(ies) on "
            f"{', '.join(meta.get('signals', []))}"
            + (
                f" [dim](already scored: {meta['already_scored']})[/dim]"
                if meta.get("already_scored")
                else ""
            )
        )
        if meta.get("missing_computers"):
            console.print(
                "[yellow]No computer for:[/yellow] "
                + ", ".join(meta["missing_computers"])
                + " -- those signals were excluded."
            )
        console.print(
            f"[cyan]{meta.get('above_minimum', 0)}[/cyan] scored at or above the "
            f"{meta.get('minimum_score')} minimum."
        )
    elif meta.get("skip_reason"):
        console.print(f"[yellow]Nothing to score:[/yellow] {meta['skip_reason']}")

    if top and not dry_run:
        _print_ranking(container, workspace, top)

    console.print(
        f"[green]Run {ledger.status.value}:[/green] {ledger.id} ({ledger.duration_seconds:.2f}s)"
    )
    if dry_run:
        console.print("[dim]Dry run -- nothing was stored.[/dim]")


@app.command("top")
def top(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    limit: int = typer.Option(10, "--limit", "-n", help="How many stories to show."),
) -> None:
    """Show the best-scoring stories, by their most recent score."""
    container = build_container()
    workspace = _require_workspace(container, slug)

    with container.unit_of_work() as uow:
        scores = uow.scores.top_recent(workspace.id, limit=limit)
        total = uow.scores.count_for_workspace(workspace.id)

    if not scores:
        console.print("[yellow]Nothing scored yet.[/yellow] Run: engine score run")
        return

    _print_ranking(container, workspace, scores)
    console.print(f"[dim]{total} score(s) recorded in total.[/dim]")


@app.command("show")
def show(
    story_id: str = typer.Argument(..., help="Story id, or its last 8 characters."),
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
) -> None:
    """Explain one story's score, signal by signal."""
    container = build_container()
    workspace = _require_workspace(container, slug)
    pack, policy = _pack_and_policy(container, workspace)
    profile = policy.scoring if not policy.scoring.is_empty else pack.default_scoring_profile

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
        score = uow.scores.latest_for_story(workspace.id, story.id)
        history = uow.scores.history_for_story(workspace.id, story.id)

    console.print(f"[bold cyan]{story.canonical_headline}[/bold cyan]")
    if score is None:
        console.print("[yellow]Not scored yet.[/yellow] Run: engine score run")
        return

    console.print(
        f"Total: [bold green]{score.total:.4f}[/bold green]  [dim]{score.rationale}[/dim]"
    )

    table = Table(title="Signals")
    table.add_column("Signal", style="cyan")
    table.add_column("Measured", justify="right")
    table.add_column("Normaliser", style="dim")
    table.add_column("Weight", justify="right")
    table.add_column("Contribution", justify="right", style="green")
    for signal in pack.ranking_signals:
        if signal.key not in score.components and signal.key not in score.raw:
            continue
        table.add_row(
            signal.key,
            f"{score.raw.get(signal.key, 0):.2f}",
            str(signal.normaliser),
            f"{profile.weight_of(signal.key):.2f}",
            f"{score.components.get(signal.key, 0):.4f}",
        )
    console.print(table)

    unmeasured = sorted(profile.signal_keys - set(score.components))
    if unmeasured:
        console.print(
            f"[dim]Not measurable for this story: {', '.join(unmeasured)} "
            "(excluded from the total rather than counted as zero).[/dim]"
        )
    if len(history) > 1:
        trend = " -> ".join(f"{entry.total:.3f}" for entry in history[-5:])
        console.print(f"[dim]Score history: {trend}[/dim]")


def _print_ranking(container: Container, workspace: Workspace, scores: list[Any]) -> None:
    with container.unit_of_work() as uow:
        stories = {story.id: story for story in uow.stories.list_recent(workspace.id, limit=1000)}

    table = Table(title="Ranking")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Score", justify="right", style="green")
    table.add_column("Headline", style="cyan", overflow="fold")
    table.add_column("Why", style="dim", overflow="fold")
    for position, score in enumerate(scores, start=1):
        story = stories.get(score.story_id)
        table.add_row(
            str(position),
            f"{score.total:.4f}",
            (story.canonical_headline if story else score.story_id)[:60],
            (score.rationale or "")[:50],
        )
    console.print(table)
