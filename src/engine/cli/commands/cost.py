"""Cost commands.

Answers the question every other part of this system exists to make askable:
**what does one post cost?** Not what the month cost -- that number is a bill,
and a bill nobody can decompose is a bill nobody can act on.

The unit is the *revision*, not the asset, because regenerating spends again.
An asset that took three attempts cost three times, and a cost-per-post figure
that hides that is the figure that makes a pricing decision wrong.
"""

from __future__ import annotations

from datetime import timedelta

import typer
from rich.console import Console
from rich.table import Table

from engine.cli.container import Container, build_container
from engine.core.domain.workspace import Workspace

console = Console()
app = typer.Typer(help="What generation costs, per asset and per run.")


def _require_workspace(container: Container, slug: str | None) -> Workspace:
    target = slug or container.settings.default_workspace_slug
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target)
    if workspace is None:
        console.print(f"[red]Workspace {target!r} not found.[/red] Run: engine workspace seed")
        raise typer.Exit(code=1)
    return workspace


@app.command("report")
def report(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    days: int = typer.Option(None, "--days", help="Only revisions from the last N days."),
    limit: int = typer.Option(10, "--limit", "-n", help="How many assets to itemise."),
) -> None:
    """Report generation spend, in total and per asset."""
    container = build_container()
    workspace = _require_workspace(container, slug)
    since = container.clock.now() - timedelta(days=days) if days else None

    with container.unit_of_work() as uow:
        total = uow.assets.total_spend(workspace.id, since=since)
        by_asset = uow.assets.spend_by_asset(workspace.id, limit=limit)
        assets = {
            spend.asset_id: uow.assets.get(workspace.id, spend.asset_id) for spend in by_asset
        }
        runs = uow.runs.list_recent(workspace.id, limit=200)

    window = f"last {days} day(s)" if days else "all time"
    if not total.revisions:
        console.print(f"[yellow]No generation spend recorded[/yellow] ({window}).")
        console.print("[dim]Run: engine generate run[/dim]")
        return

    console.print(
        f"[green]${total.cost_usd:.4f}[/green] across {total.revisions} generation(s) "
        f"({window})   [dim]{total.input_tokens} in / {total.output_tokens} out[/dim]"
    )
    console.print(
        f"[dim]Mean per generation: ${total.cost_per_revision_usd:.6f}. "
        f"Ceiling per run: ${container.settings.max_cost_usd_per_run:.2f}.[/dim]"
    )
    if total.cost_usd == 0.0:
        # Otherwise a table of zeroes reads as broken instrumentation rather
        # than as the accurate cost of running a model on your own hardware.
        console.print(
            f"[dim]Zero because the provider is local "
            f"({container.settings.llm_provider}). See docs/LLM.md.[/dim]"
        )

    table = Table(title="Most expensive assets")
    table.add_column("Asset", style="dim", overflow="fold")
    table.add_column("Channel")
    table.add_column("Lang")
    table.add_column("Attempts", justify="right")
    table.add_column("Out tokens", justify="right")
    table.add_column("Cost", justify="right", style="green")
    for spend in by_asset:
        asset = assets.get(spend.asset_id)
        table.add_row(
            spend.asset_id,
            (asset.channel_key or "-") if asset else "-",
            str(asset.language) if asset else "-",
            str(spend.revisions),
            str(spend.output_tokens),
            f"${spend.cost_usd:.6f}",
        )
    console.print(table)

    generated = [run for run in runs if run.pipeline == "generate"][:limit]
    if not generated:
        return
    runs_table = Table(title="Recent generate runs")
    runs_table.add_column("Started (UTC)")
    runs_table.add_column("Status")
    runs_table.add_column("Out tokens", justify="right")
    runs_table.add_column("Cost", justify="right", style="green")
    for run in generated:
        runs_table.add_row(
            run.started_at.strftime("%Y-%m-%d %H:%M"),
            run.status.value,
            str(run.usage.output_tokens),
            f"${run.cost_usd:.6f}",
        )
    console.print(runs_table)
