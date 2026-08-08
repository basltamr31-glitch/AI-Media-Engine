"""Review commands: the human gate between generation and publication.

``review queue`` is the working surface -- what is waiting for a decision --
and everything else acts on one asset at a time. The commands are thin: the
decisions themselves live in :mod:`engine.cli.lifecycle`, because a state
transition plus a revision plus a signal is the part worth testing, and a
Typer callback is not.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from engine.cli.container import Container, build_container
from engine.cli.lifecycle import LifecycleError, ReviewItem
from engine.cli.lifecycle import approve as approve_asset
from engine.cli.lifecycle import edit as edit_asset
from engine.cli.lifecycle import queue as review_queue
from engine.cli.lifecycle import reject as reject_asset
from engine.cli.lifecycle import show as show_asset
from engine.cli.provisioning import actor_by_key
from engine.core.domain.account import OWNER_ACTOR_KEY, Actor
from engine.core.domain.content import normalise_hashtags
from engine.core.domain.enums import ContentState
from engine.core.domain.workspace import Workspace

console = Console()
app = typer.Typer(help="Review generated content and decide what ships.")

#: Colours by state, so a queue can be read without reading it.
_STATE_STYLE = {
    ContentState.DRAFT: "white",
    ContentState.IN_REVIEW: "yellow",
    ContentState.APPROVED: "green",
    ContentState.REJECTED: "red",
    ContentState.PUBLISHED: "cyan",
    ContentState.ARCHIVED: "dim",
}


def _require_workspace(container: Container, slug: str | None) -> Workspace:
    target = slug or container.settings.default_workspace_slug
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target)
    if workspace is None:
        console.print(f"[red]Workspace {target!r} not found.[/red] Run: engine workspace seed")
        raise typer.Exit(code=1)
    return workspace


def _require_actor(container: Container, workspace: Workspace, key: str) -> Actor:
    """Resolve who is making this decision, refusing to invent them."""
    with container.unit_of_work() as uow:
        try:
            return actor_by_key(uow, workspace.account_id, key)
        except LookupError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc


def _fail(exc: LifecycleError) -> typer.Exit:
    console.print(f"[red]{exc}[/red]")
    return typer.Exit(code=1)


@app.command("queue")
def queue(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    state: str = typer.Option(
        None,
        "--state",
        help="Filter by state. Default: draft and in-review, i.e. what awaits you.",
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="How many to show."),
) -> None:
    """Show the content waiting for a decision."""
    container = build_container()
    workspace = _require_workspace(container, slug)

    wanted: ContentState | None = None
    if state is not None:
        try:
            wanted = ContentState(state)
        except ValueError as exc:
            options = ", ".join(member.value for member in ContentState)
            console.print(f"[red]Unknown state {state!r}.[/red] One of: {options}")
            raise typer.Exit(code=1) from exc

    with container.unit_of_work() as uow:
        items = review_queue(uow, workspace.id, state=wanted, limit=limit)

    if not items:
        console.print("[yellow]Nothing waiting.[/yellow] Generate some: engine generate run")
        return

    table = Table(title=f"Review queue -- {workspace.name}")
    table.add_column("Asset", style="dim", no_wrap=True)
    table.add_column("State")
    table.add_column("Channel")
    table.add_column("Lang", justify="center")
    table.add_column("Chars", justify="right")
    table.add_column("Headline", style="cyan", overflow="fold")
    for item in items:
        asset = item.asset
        table.add_row(
            asset.id,
            f"[{_STATE_STYLE[asset.state]}]{asset.state.value}[/]",
            asset.channel_key or "-",
            str(asset.language),
            str(item.characters),
            (item.headline or "[dim]story missing[/dim]")[:70],
        )
    console.print(table)
    console.print("[dim]Read one: engine review show <asset-id>[/dim]")


@app.command("show")
def show(
    asset_id: str = typer.Argument(..., help="Asset id from the queue."),
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    history: bool = typer.Option(False, "--history", help="Show the whole revision chain."),
) -> None:
    """Show one asset's current text, and optionally how it got there."""
    container = build_container()
    workspace = _require_workspace(container, slug)

    with container.unit_of_work() as uow:
        try:
            item = show_asset(uow, workspace.id, asset_id)
        except LifecycleError as exc:
            raise _fail(exc) from exc
        revisions = uow.assets.list_revisions(workspace.id, asset_id) if history else []

    _print_asset(item)

    if history and revisions:
        table = Table(title="Revisions")
        table.add_column("#", justify="right", style="dim")
        table.add_column("Origin")
        table.add_column("Model")
        table.add_column("Chars", justify="right")
        table.add_column("Cost", justify="right")
        for revision in revisions:
            table.add_row(
                str(revision.revision_number),
                revision.origin.value,
                revision.model or "-",
                str(len(revision.body)),
                f"${revision.cost_usd:.4f}",
            )
        console.print(table)


def _print_asset(item: ReviewItem) -> None:
    asset = item.asset
    console.print(
        Panel(
            item.body or "[dim]no text[/dim]",
            title=item.headline or asset.id,
            subtitle=(
                f"{asset.channel_key or 'no channel'} / {asset.language} / "
                f"{asset.state.value} / {item.characters} chars"
                + (" / edited" if item.was_edited else "")
            ),
        )
    )
    hashtags = normalise_hashtags(asset.meta.get("hashtags"))
    if hashtags:
        console.print("Hashtags: " + " ".join(f"#{tag}" for tag in hashtags))
    if asset.review_notes:
        console.print(f"[dim]Notes: {asset.review_notes}[/dim]")


@app.command("approve")
def approve(
    asset_id: str = typer.Argument(..., help="Asset id from the queue."),
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    note: str = typer.Option(None, "--note", help="Optional note recorded with the approval."),
    actor_key: str = typer.Option(OWNER_ACTOR_KEY, "--as", help="Actor key making the decision."),
) -> None:
    """Clear an asset for publication."""
    container = build_container()
    workspace = _require_workspace(container, slug)
    actor = _require_actor(container, workspace, actor_key)

    with container.unit_of_work() as uow:
        try:
            asset = approve_asset(
                uow, workspace.id, asset_id, actor=actor, clock=container.clock, notes=note
            )
        except LifecycleError as exc:
            raise _fail(exc) from exc
        uow.commit()

    console.print(f"[green]Approved[/green] {asset.id} as {actor.display_name}")
    console.print("[dim]Then: engine export run, and engine publish record <asset-id>[/dim]")


@app.command("reject")
def reject(
    asset_id: str = typer.Argument(..., help="Asset id from the queue."),
    reason: str = typer.Option(..., "--reason", help="Why. Required: this is the training data."),
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    actor_key: str = typer.Option(OWNER_ACTOR_KEY, "--as", help="Actor key making the decision."),
) -> None:
    """Refuse an asset, recording why."""
    container = build_container()
    workspace = _require_workspace(container, slug)
    actor = _require_actor(container, workspace, actor_key)

    with container.unit_of_work() as uow:
        try:
            asset = reject_asset(
                uow, workspace.id, asset_id, actor=actor, clock=container.clock, reason=reason
            )
        except LifecycleError as exc:
            raise _fail(exc) from exc
        uow.commit()

    console.print(f"[red]Rejected[/red] {asset.id}: {reason}")


@app.command("edit")
def edit(
    asset_id: str = typer.Argument(..., help="Asset id from the queue."),
    file: Path = typer.Option(
        None, "--file", "-f", help="File holding the new body, UTF-8 encoded."
    ),
    body: str = typer.Option(None, "--body", help="The new body, inline."),
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    actor_key: str = typer.Option(OWNER_ACTOR_KEY, "--as", help="Actor key making the edit."),
) -> None:
    """Replace an asset's text, keeping the model's version alongside.

    A file is usually easier than ``--body``: the text is a paragraph, often
    Arabic, and shell quoting is a poor editor.
    """
    if (file is None) == (body is None):
        console.print("[red]Give exactly one of --file or --body.[/red]")
        raise typer.Exit(code=1)

    if file is not None:
        if not file.is_file():
            console.print(f"[red]No such file:[/red] {file}")
            raise typer.Exit(code=1)
        # Explicit UTF-8: the default encoding on Windows is not, and an Arabic
        # rewrite read through it becomes mojibake that looks like a model bug.
        body = file.read_text(encoding="utf-8")

    container = build_container()
    workspace = _require_workspace(container, slug)
    actor = _require_actor(container, workspace, actor_key)

    with container.unit_of_work() as uow:
        try:
            outcome = edit_asset(
                uow,
                workspace.id,
                asset_id,
                actor=actor,
                clock=container.clock,
                body=body or "",
            )
        except LifecycleError as exc:
            raise _fail(exc) from exc
        uow.commit()

    delta = outcome.delta
    verb = "Rewrote" if delta.is_rewrite else "Edited"
    console.print(
        f"[green]{verb}[/green] {outcome.asset.id} -> revision {outcome.revision.revision_number} "
        f"({delta.similarity:.0%} similar, {delta.characters_changed:+d} chars)"
    )
    if outcome.returned_to_draft:
        console.print("[dim]Back to draft: approval refers to the text that was approved.[/dim]")
