"""Export commands.

``export run`` writes every approved post as a file containing exactly the text
to paste. ``export show`` reads one back without opening a folder.

The useful output is the *warnings*: a post that is over the channel limit or
had hashtags dropped still exports, because refusing to write the file would
leave an operator with nothing and no explanation. What they get instead is the
file and a list of what to fix.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from engine.cli.container import Container, build_container
from engine.core.domain.workspace import Workspace
from engine.core.ports.packs import PackLoadError
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.export import EXPORT_DIRNAME, MANIFEST_NAME, ExportStage

console = Console()
app = typer.Typer(help="Write approved posts as publishable files.")


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
        None, "--edition", help="Override the folder date, e.g. 2026-08-08."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report without writing files."),
) -> None:
    """Write every approved post to its own file."""
    container = build_container()
    workspace = _require_workspace(container, slug)

    try:
        pack = container.packs.load(workspace.pack_key)
    except PackLoadError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    stage = ExportStage(
        output_dir=container.resolve_path(container.settings.output_dir),
        workspace_slug=workspace.slug,
        timezone=workspace.timezone,
        pack=pack,
        edition=edition,
    )
    runner = PipelineRunner(
        uow=container.unit_of_work(), clock=container.clock, settings=container.settings
    )

    try:
        ledger = runner.run(
            workspace_id=workspace.id,
            pipeline="export",
            stages=[stage],
            trigger="cli",
            dry_run=dry_run,
        )
    except Exception as exc:
        console.print(f"[red]Export failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    with container.unit_of_work() as uow:
        records = uow.runs.list_stages(workspace.id, ledger.id)
    meta: dict[str, Any] = next((record.meta for record in records if record.stage == "export"), {})

    if not meta or meta.get("skip_reason"):
        console.print(
            f"[yellow]Nothing exported:[/yellow] {meta.get('skip_reason', 'nothing approved')}"
        )
        console.print("[dim]Approve something first: engine review queue[/dim]")
        return

    console.print(
        f"[green]Exported[/green] {meta['posts']} post(s) "
        f"across {', '.join(meta['channels']) or 'no channel'} -> {meta['directory']}"
    )
    if meta.get("with_warnings"):
        console.print(
            f"[yellow]{meta['with_warnings']} need attention.[/yellow] "
            f"See {MANIFEST_NAME}, or: engine export show"
        )
    if dry_run:
        console.print("[dim]Dry run -- nothing was written.[/dim]")


@app.command("show")
def show(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    edition: str = typer.Option(None, "--edition", help="Which folder to read."),
) -> None:
    """List what an export folder contains, and what is wrong with it."""
    container = build_container()
    workspace = _require_workspace(container, slug)

    root = container.resolve_path(container.settings.output_dir) / workspace.slug
    directory = _resolve_edition(root, edition)
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.is_file():
        console.print(f"[red]No export at {directory}.[/red] Run: engine export run")
        raise typer.Exit(code=1)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    posts = manifest.get("posts", [])

    table = Table(title=f"Export -- {manifest.get('edition')}")
    table.add_column("File", style="dim", overflow="fold")
    table.add_column("Channel")
    table.add_column("Lang", justify="center")
    table.add_column("Chars", justify="right")
    table.add_column("OK", justify="center")
    table.add_column("Headline", style="cyan", overflow="fold")
    for post in posts:
        table.add_row(
            post["file"],
            post["channel"],
            post["language"],
            str(post["characters"]),
            "[green]yes[/green]" if post["publishable"] else "[yellow]no[/yellow]",
            (post["headline"] or "")[:50],
        )
    console.print(table)

    for post in posts:
        for warning in post["warnings"]:
            console.print(f"[yellow]{post['file']}:[/yellow] {warning}")


def _resolve_edition(root: Path, edition: str | None) -> Path:
    """The named export folder, or the most recent one."""
    if edition:
        return root / edition / EXPORT_DIRNAME
    if not root.is_dir():
        return root / "none" / EXPORT_DIRNAME
    # Edition folders are ISO dates, so lexical order is chronological order.
    editions = sorted(
        (path for path in root.iterdir() if (path / EXPORT_DIRNAME / MANIFEST_NAME).is_file()),
        reverse=True,
    )
    if not editions:
        return root / "none" / EXPORT_DIRNAME
    return editions[0] / EXPORT_DIRNAME
