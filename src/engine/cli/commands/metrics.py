"""Performance commands -- the closing move of the feedback loop.

``metrics add`` records one engagement observation; ``metrics report`` shows
what published content actually achieved. Entry is by hand in V1, and that is a
deliberate ordering rather than a shortcut: a number typed in after a week
closes the loop, whereas per-platform OAuth is weeks of work that closes
nothing until the last one lands.

The schema does not know the difference. ``collected_by`` is the only field
that changes when collection is automated, so the report, the signals and
anything learning from them keep working untouched.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

import typer
from rich.console import Console
from rich.table import Table

from engine.cli.container import Container, build_container
from engine.cli.lifecycle import LifecycleError, record_metric
from engine.cli.provisioning import actor_by_key
from engine.core.domain.account import OWNER_ACTOR_KEY, Actor
from engine.core.domain.content import PublicationPerformance
from engine.core.domain.workspace import Workspace

console = Console()
app = typer.Typer(help="Record and report what published content achieved.")


def _require_workspace(container: Container, slug: str | None) -> Workspace:
    target = slug or container.settings.default_workspace_slug
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target)
    if workspace is None:
        console.print(f"[red]Workspace {target!r} not found.[/red] Run: engine workspace seed")
        raise typer.Exit(code=1)
    return workspace


def _require_actor(container: Container, workspace: Workspace, key: str) -> Actor:
    with container.unit_of_work() as uow:
        try:
            return actor_by_key(uow, workspace.account_id, key)
        except LookupError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc


def _parse_when(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError as exc:
        console.print(
            f"[red]Cannot read {raw!r} as a date.[/red] Try 2026-08-08 or 2026-08-08T14:30"
        )
        raise typer.Exit(code=1) from exc
    return when if when.tzinfo else when.replace(tzinfo=UTC)


@app.command("add")
def add(
    publication_id: str = typer.Argument(..., help="Publication from: engine publish list."),
    metric: str = typer.Option(..., "--metric", "-m", help="impressions, likes, shares, ..."),
    value: float = typer.Option(..., "--value", "-v", help="What it read."),
    at: str = typer.Option(None, "--at", help="When it was read. Defaults to now."),
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    actor_key: str = typer.Option(OWNER_ACTOR_KEY, "--as", help="Actor key recording this."),
) -> None:
    """Record one engagement reading.

    Reading the same metric again later is expected, not a mistake: two
    readings are two facts, and keeping both is what makes growth over time
    recoverable.
    """
    container = build_container()
    workspace = _require_workspace(container, slug)
    actor = _require_actor(container, workspace, actor_key)
    when = _parse_when(at)

    with container.unit_of_work() as uow:
        try:
            observation = record_metric(
                uow,
                workspace.id,
                publication_id,
                actor=actor,
                clock=container.clock,
                metric=metric,
                value=value,
                captured_at=when,
            )
        except LifecycleError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        uow.commit()

    console.print(
        f"[green]Recorded[/green] {observation.metric}={observation.value:g} "
        f"for {publication_id} at {observation.captured_at.isoformat()}"
    )


@app.command("report")
def report(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    limit: int = typer.Option(20, "--limit", "-n", help="How many publications to include."),
) -> None:
    """Show what published content achieved, and what has not been measured."""
    container = build_container()
    workspace = _require_workspace(container, slug)

    with container.unit_of_work() as uow:
        rows = uow.metrics.performance(workspace.id, limit=limit)
        context = {}
        for row in rows:
            asset = uow.assets.get(workspace.id, row.asset_id)
            story = uow.stories.get(workspace.id, asset.story_id) if asset else None
            context[row.publication_id] = (
                str(asset.language) if asset else "",
                story.canonical_headline if story else "",
            )

    if not rows:
        console.print("[yellow]Nothing published yet.[/yellow]")
        console.print("[dim]Record one: engine publish record <asset-id>[/dim]")
        return

    # The union of metric names, so a publication measured on impressions and
    # one measured on likes appear in the same table instead of two.
    names = sorted({name for row in rows for name in row.metrics})

    table = Table(title=f"Engagement -- {workspace.name}")
    table.add_column("Published", no_wrap=True)
    table.add_column("Channel")
    table.add_column("Lang", justify="center")
    for name in names:
        table.add_column(name, justify="right")
    table.add_column("Headline", style="cyan", overflow="fold")

    for row in rows:
        language, headline = context.get(row.publication_id, ("", ""))
        table.add_row(
            row.published_at.date().isoformat(),
            row.channel_key,
            language,
            *[f"{row.value_of(name):g}" if name in row.metrics else "-" for name in names],
            headline[:40],
        )
    console.print(table)

    _print_by_channel(rows, names)

    unmeasured = [row for row in rows if not row.has_data]
    if unmeasured:
        console.print(
            f"[yellow]{len(unmeasured)} of {len(rows)} publication(s) "
            "have no readings yet.[/yellow]"
        )
        console.print("[dim]Add one: engine metrics add <publication-id> -m likes -v 12[/dim]")


def _print_by_channel(rows: list[PublicationPerformance], names: list[str]) -> None:
    """Totals per channel -- the aggregate that answers 'where does this work?'.

    Summed rather than averaged: a channel with three posts and one with thirty
    are not comparable on an average, and the post count is shown so nobody
    reads the totals as if they were.
    """
    measured = [row for row in rows if row.has_data]
    if not measured or not names:
        return

    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    counts: dict[str, int] = defaultdict(int)
    for row in measured:
        counts[row.channel_key] += 1
        for name, value in row.metrics.items():
            totals[row.channel_key][name] += value

    table = Table(title="By channel")
    table.add_column("Channel")
    table.add_column("Posts", justify="right")
    for name in names:
        table.add_column(name, justify="right")
    for channel in sorted(totals):
        table.add_row(
            channel,
            str(counts[channel]),
            *[f"{totals[channel].get(name, 0.0):g}" for name in names],
        )
    console.print(table)
