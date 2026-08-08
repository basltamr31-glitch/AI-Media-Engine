"""Plugin inspection.

An extension mechanism nobody can debug is worse than no extension mechanism
(ADR 0017). "Why is my fetcher not registered?" has to be answerable in one
command, and the answer has to come from the same discovery pass the engine
actually ran -- not from a parallel one that could disagree with it.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from engine.adapters.plugins.discovery import PLUGIN_GROUPS, PluginStatus
from engine.cli.container import build_container

console = Console()
app = typer.Typer(help="Installed engine plugins.")


@app.command("list")
def list_plugins(
    group: str = typer.Option(None, "--group", help="Restrict to one entry-point group."),
) -> None:
    """Show every discovered plugin, what loaded, and what failed and why."""
    container = build_container()
    records = container.plugin_records()
    if group:
        # Accept either "fetchers" or the full "engine.fetchers": the table
        # shows the short form, so the short form has to work as a filter.
        wanted = group if group.startswith("engine.") else f"engine.{group}"
        records = [record for record in records if record.group == wanted]

    if not records:
        scope = f" in group {group!r}" if group else ""
        console.print(f"[yellow]No plugins found{scope}.[/yellow]")
        console.print(f"Known groups: {', '.join(PLUGIN_GROUPS)}")
        return

    table = Table(title='Plugins (entry-point groups, all prefixed "engine.")')
    table.add_column("Group", style="dim")
    table.add_column("Name", style="cyan")
    table.add_column("Provider")
    table.add_column("Entry point", style="dim", overflow="fold")
    table.add_column("Status")
    table.add_column("Detail", overflow="fold")
    for record in records:
        failed = record.status is PluginStatus.FAILED
        table.add_row(
            record.group.removeprefix("engine."),
            record.name,
            record.provider,
            record.value,
            f"[red]{record.status}[/red]" if failed else f"[green]{record.status}[/green]",
            record.error or "",
        )
    console.print(table)

    failures = [record for record in records if record.failed]
    if failures:
        console.print(
            f"[red]{len(failures)} plugin(s) failed to load[/red] and were skipped; "
            "the engine is running without them."
        )
