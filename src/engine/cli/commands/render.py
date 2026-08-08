"""Rendering commands.

``render run`` writes the day's edition; ``render show`` prints what is in one
without opening two files.

The edition folder is the hand-off point between this engine and whatever
publishes: after this command, nothing downstream needs a database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from engine.adapters.config.policy_loader import PolicyLoadError
from engine.cli.container import Container, build_container
from engine.core.domain.workspace import Workspace
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.render import PACKAGE_NAME, POST_NAME, RenderStage

console = Console()
app = typer.Typer(help="Write the day's edition to disk.")


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
    edition: str = typer.Option(
        None, "--edition", help="Override the edition date, e.g. 2026-08-08."
    ),
    window: int = typer.Option(None, "--window", help="Hours of selections to include."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report without writing files."),
) -> None:
    """Write package.json and post.md for the current edition."""
    container = build_container()
    workspace = _require_workspace(container, slug)
    settings = container.settings

    try:
        config = container.policy_loader.load_workspace(workspace.slug)
    except PolicyLoadError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    stage = RenderStage(
        output_dir=container.resolve_path(settings.output_dir),
        workspace_slug=workspace.slug,
        timezone=config.timezone or workspace.timezone,
        window_hours=window or settings.select_window_hours,
        edition=edition,
    )
    runner = PipelineRunner(uow=container.unit_of_work(), clock=container.clock, settings=settings)

    try:
        ledger = runner.run(
            workspace_id=workspace.id,
            pipeline="render",
            stages=[stage],
            trigger="cli",
            dry_run=dry_run,
        )
    except Exception as exc:
        console.print(f"[red]Rendering failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    with container.unit_of_work() as uow:
        records = uow.runs.list_stages(workspace.id, ledger.id)
    meta: dict[str, Any] = next((r.meta for r in records if r.stage == "render"), {})

    if not meta:
        console.print("[yellow]Nothing rendered.[/yellow]")
        raise typer.Exit(code=0)

    console.print(
        f"Edition [cyan]{meta['edition']}[/cyan]: "
        f"[green]{meta['stories']}[/green] story(ies), "
        f"[green]{meta['assets']}[/green] post(s)"
    )
    console.print(f"  {meta['directory']}")
    if not meta["stories"]:
        console.print("[dim]A thin day. Run: engine select run, then engine generate run.[/dim]")
    if dry_run:
        console.print("[dim]Dry run -- nothing was written.[/dim]")


@app.command("show")
def show(
    edition: str = typer.Argument(None, help="Edition date. Defaults to the most recent."),
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
) -> None:
    """Summarise a rendered edition from its package."""
    container = build_container()
    workspace = _require_workspace(container, slug)
    root = container.resolve_path(container.settings.output_dir) / workspace.slug

    if not root.is_dir():
        console.print(
            f"[yellow]No editions for {workspace.slug!r}.[/yellow] Run: engine render run"
        )
        raise typer.Exit(code=0)

    directory = root / edition if edition else _latest(root)
    if directory is None or not (directory / PACKAGE_NAME).is_file():
        console.print(f"[red]No edition at {directory}.[/red]")
        raise typer.Exit(code=1)

    package = json.loads((directory / PACKAGE_NAME).read_text(encoding="utf-8"))
    console.print(
        f"Edition [cyan]{package['edition']}[/cyan] "
        f"[dim](schema {package['schema_version']}, engine {package['engine_version']}, "
        f"${package['cost_usd']:.4f})[/dim]"
    )
    console.print(f"  {directory / POST_NAME}")

    table = Table(title="Posts")
    table.add_column("Story", style="cyan", overflow="fold")
    table.add_column("Channel")
    table.add_column("Lang")
    table.add_column("Chars", justify="right")
    for story in package["stories"]:
        for asset in story["assets"]:
            table.add_row(
                story["headline"][:45],
                asset["channel"] or asset["product"],
                asset["language"],
                str(asset["characters"]),
            )
    console.print(table)


def _latest(root: Path) -> Path | None:
    """The most recent edition. Dates sort lexically, which is why they are ISO."""
    editions = sorted((path for path in root.iterdir() if path.is_dir()), reverse=True)
    return editions[0] if editions else None
