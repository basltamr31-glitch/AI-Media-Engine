"""Domain pack inspection.

``pack validate`` exists because packs are data, so a typo that a Python file
would have caught at import time is now a runtime concern. Validating in CI
restores the guarantee.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from engine.cli.container import build_container
from engine.core.domain.pack import DomainPack, TaxonomyNode
from engine.core.ports.packs import PackLoadError, PackNotFoundError

console = Console()
app = typer.Typer(help="Domain packs -- the subject knowledge the engine lacks.")


@app.command("list")
def list_packs() -> None:
    """List available domain packs."""
    container = build_container()
    keys = container.packs.list_keys()

    if not keys:
        console.print(
            f"[yellow]No packs found in {container.resolve_path(container.settings.packs_dir)}"
            "[/yellow]"
        )
        return

    with container.unit_of_work() as uow:
        workspaces = uow.workspaces.list_all(include_inactive=True)
    in_use: dict[str, list[str]] = {}
    for workspace in workspaces:
        in_use.setdefault(workspace.pack_key, []).append(workspace.slug)

    table = Table(title="Domain packs")
    table.add_column("Key", style="cyan")
    table.add_column("Version", justify="right")
    table.add_column("Name")
    table.add_column("Categories", justify="right")
    table.add_column("Products", justify="right")
    table.add_column("Channels", justify="right")
    table.add_column("Signals", justify="right")
    table.add_column("Used by")

    for key in keys:
        try:
            pack = container.packs.load(key)
        except (PackNotFoundError, PackLoadError) as exc:
            table.add_row(key, "-", "[red]invalid[/red]", "-", "-", "-", "-", str(exc)[:40])
            continue
        table.add_row(
            pack.key,
            str(pack.version),
            pack.name,
            str(len(pack.taxonomy)),
            str(len(pack.products)),
            str(len(pack.channels)),
            str(len(pack.ranking_signals)),
            ", ".join(in_use.get(pack.key, [])) or "[dim]-[/dim]",
        )
    console.print(table)


@app.command("show")
def show_pack(
    key: str | None = typer.Argument(None, help="Pack key (default: the active workspace's pack)."),
) -> None:
    """Show a pack's contents, including its taxonomy tree."""
    container = build_container()

    target = key
    if target is None:
        with container.unit_of_work() as uow:
            workspace = uow.workspaces.get_by_slug(container.settings.default_workspace_slug)
        if workspace is None:
            console.print("[red]No workspace to infer a pack from.[/red] Pass a pack key.")
            raise typer.Exit(code=1)
        target = workspace.pack_key

    try:
        pack = container.packs.load(target)
    except (PackNotFoundError, PackLoadError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]{pack.name}[/green] ([cyan]{pack.identifier}[/cyan])")
    if pack.description:
        console.print(f"[dim]{pack.description}[/dim]")

    if pack.taxonomy:
        console.print(_taxonomy_tree(pack))
    else:
        console.print("[yellow]No taxonomy defined.[/yellow]")

    if pack.products:
        products = Table(title="Output products")
        products.add_column("Key", style="cyan")
        products.add_column("Name")
        products.add_column("Kind")
        for product in pack.products:
            products.add_row(product.key, product.name, product.kind.value)
        console.print(products)

    if pack.channels:
        channels = Table(title="Channels")
        channels.add_column("Key", style="cyan")
        channels.add_column("Name")
        channels.add_column("Max chars", justify="right")
        channels.add_column("Hashtags", justify="right")
        channels.add_column("Links")
        channels.add_column("Products")
        for channel in pack.channels:
            channels.add_row(
                channel.key,
                channel.name,
                str(channel.max_characters),
                str(channel.max_hashtags) if channel.supports_hashtags else "no",
                "yes" if channel.supports_links else "no",
                ", ".join(channel.preferred_products) or "-",
            )
        console.print(channels)

    if pack.ranking_signals:
        signals = Table(title="Ranking signals")
        signals.add_column("Key", style="cyan")
        signals.add_column("Name")
        signals.add_column("Normaliser")
        signals.add_column("Default weight", justify="right")
        signals.add_column("Parameters", style="dim")
        for signal in pack.ranking_signals:
            signals.add_row(
                signal.key,
                signal.name,
                signal.normaliser.value,
                f"{signal.default_weight:.2f}",
                ", ".join(f"{k}={v:g}" for k, v in signal.parameters.items()) or "-",
            )
        console.print(signals)


def _taxonomy_tree(pack: DomainPack) -> Tree:
    tree = Tree(f"Taxonomy ({len(pack.taxonomy)} categories)")

    def add(parent_branch: Tree, node: TaxonomyNode) -> None:
        label = f"[cyan]{node.key}[/cyan] — {node.label}"
        branch = parent_branch.add(label)
        for child in pack.children_of(node.key):
            add(branch, child)

    for root in pack.roots():
        add(tree, root)
    return tree


@app.command("validate")
def validate_packs(
    key: str | None = typer.Argument(None, help="Pack key (default: validate every pack)."),
) -> None:
    """Validate pack manifests. Exits non-zero on failure, for use in CI."""
    container = build_container()
    keys = [key] if key else container.packs.list_keys()

    if not keys:
        console.print("[yellow]No packs to validate.[/yellow]")
        return

    failures = 0
    for candidate in keys:
        try:
            pack = container.packs.load(candidate)
        except (PackNotFoundError, PackLoadError) as exc:
            console.print(f"[red]FAIL[/red] {candidate}: {exc}")
            failures += 1
        else:
            console.print(
                f"[green]OK[/green]   {pack.identifier} "
                f"({len(pack.taxonomy)} categories, {len(pack.products)} products, "
                f"{len(pack.channels)} channels, "
                f"{len(pack.ranking_signals)} ranking signals)"
            )

    if failures:
        console.print(f"\n[red]{failures} pack(s) failed validation.[/red]")
        raise typer.Exit(code=1)
    console.print(f"\n[green]{len(keys)} pack(s) valid.[/green]")
