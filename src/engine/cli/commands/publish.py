"""Publication commands.

``publish record`` states that an approved post went live, and where. The
engine does not post anything itself (ADR 0018): no platform OAuth, no
scheduling, no automated posting. What it does is keep the record that turns
"we published something" into a row that engagement can be attached to.

That distinction is the whole reason this command exists rather than a
publisher integration. The feedback loop needs the *link* between an asset and
an outcome; who pressed the button is not the interesting part.
"""

from __future__ import annotations

from datetime import UTC, datetime

import typer
from rich.console import Console
from rich.table import Table

from engine.cli.container import Container, build_container
from engine.cli.lifecycle import LifecycleError, record_publication
from engine.cli.provisioning import actor_by_key
from engine.core.domain.account import OWNER_ACTOR_KEY, Actor
from engine.core.domain.workspace import Workspace

console = Console()
app = typer.Typer(help="Record that approved content went live.")


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
    """Read a publication time, defaulting a bare date to midnight UTC.

    Backdating is expected: posts get recorded after the fact, and a
    ``published_at`` of "whenever I got round to typing this" would corrupt
    every timing question the log exists to answer.
    """
    if raw is None:
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError as exc:
        console.print(
            f"[red]Cannot read {raw!r} as a date.[/red] Try 2026-08-08 or 2026-08-08T14:30"
        )
        raise typer.Exit(code=1) from exc
    # A naive value would compare unpredictably against stored UTC timestamps.
    return when if when.tzinfo else when.replace(tzinfo=UTC)


@app.command("record")
def record(
    asset_id: str = typer.Argument(..., help="Approved asset that went live."),
    url: str = typer.Option(None, "--url", help="Where it can be read."),
    external_id: str = typer.Option(None, "--id", help="The platform's own id for the post."),
    at: str = typer.Option(None, "--at", help="When it went live. Defaults to now."),
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    actor_key: str = typer.Option(OWNER_ACTOR_KEY, "--as", help="Actor key recording this."),
) -> None:
    """Record that an approved asset was published."""
    container = build_container()
    workspace = _require_workspace(container, slug)
    actor = _require_actor(container, workspace, actor_key)
    when = _parse_when(at)

    with container.unit_of_work() as uow:
        try:
            publication = record_publication(
                uow,
                workspace.id,
                asset_id,
                actor=actor,
                clock=container.clock,
                external_url=url,
                external_id=external_id,
                published_at=when,
            )
        except LifecycleError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        uow.commit()

    console.print(
        f"[green]Recorded[/green] {publication.id} on {publication.channel_key} "
        f"at {publication.published_at.isoformat()}"
    )
    console.print(f"[dim]Then, once it has run: engine metrics add {publication.id} ...[/dim]")


@app.command("list")
def list_publications(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    limit: int = typer.Option(20, "--limit", "-n", help="How many to show."),
) -> None:
    """Show what has been published, newest first."""
    container = build_container()
    workspace = _require_workspace(container, slug)

    with container.unit_of_work() as uow:
        publications = uow.publications.list_recent(workspace.id, limit=limit)
        headlines = {}
        for publication in publications:
            asset = uow.assets.get(workspace.id, publication.asset_id)
            story = uow.stories.get(workspace.id, asset.story_id) if asset else None
            headlines[publication.id] = story.canonical_headline if story else ""

    if not publications:
        console.print("[yellow]Nothing published yet.[/yellow]")
        console.print("[dim]Record one: engine publish record <asset-id> --url ...[/dim]")
        return

    table = Table(title=f"Published -- {workspace.name}")
    table.add_column("Publication", style="dim", no_wrap=True)
    table.add_column("Channel")
    table.add_column("When")
    table.add_column("URL", overflow="fold")
    table.add_column("Headline", style="cyan", overflow="fold")
    for publication in publications:
        table.add_row(
            publication.id,
            publication.channel_key,
            publication.published_at.date().isoformat(),
            publication.external_url or "-",
            headlines[publication.id][:45],
        )
    console.print(table)
