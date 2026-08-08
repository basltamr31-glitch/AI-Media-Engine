"""Generation commands.

``generate run`` writes the posts; ``generate list`` shows what exists;
``generate show`` prints one asset with its revision history.

The third is not a convenience. The revision chain is where the difference
between what the model wrote and what a human published is kept, and a chain
nobody can read is a chain nobody will trust to be worth keeping.
"""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from engine.adapters.config.policy_loader import PolicyLoadError
from engine.cli.container import Container, build_container
from engine.core.domain.content import BrandProfile
from engine.core.domain.pack import DomainPack
from engine.core.domain.workspace import Workspace
from engine.core.ports.packs import PackLoadError, PackNotFoundError
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.generate import COST_KEY, HASHTAGS_KEY, HOOK_KEY, GenerateStage

console = Console()
app = typer.Typer(help="Write posts from selected stories.")

#: Plain-English names for the stage's refusal codes.
_REASONS = {
    "no_primary_source": "no primary source to write from",
    "no_brand_profile": "no brand profile in that language",
    "over_channel_limit": "the model wrote past the channel's character limit",
    "model_failed": "the model call failed",
}

#: Provider failure codes, which arrive qualified as ``model_failed:<code>``.
#: Each names the fix, because that is the whole reason they are kept apart.
_MODEL_REASONS = {
    "timeout": "the model ran out of time -- raise ENGINE_OLLAMA_TIMEOUT_SECONDS",
    "provider_unreachable": "the provider is not reachable -- is Ollama running?",
    "provider_unavailable": "the provider rejected the request",
    "model_not_found": "the model is not installed -- run: ollama pull <model>",
    "schema_violation": "the model could not hold the output format",
    "malformed_response": "the provider returned something that was not JSON",
    "max_tokens": "the model hit the token ceiling -- raise ENGINE_GENERATE_MAX_TOKENS",
    "empty_response": "the model returned nothing",
    "refusal": "the model declined to answer",
}


def _describe(code: str) -> str:
    """Turn a refusal code into something an operator can act on."""
    category, _, detail = code.partition(":")
    if not detail:
        return _REASONS.get(code, code)
    return _MODEL_REASONS.get(detail, f"{_REASONS.get(category, category)} ({detail})")


def _require_workspace(container: Container, slug: str | None) -> Workspace:
    target = slug or container.settings.default_workspace_slug
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target)
    if workspace is None:
        console.print(f"[red]Workspace {target!r} not found.[/red] Run: engine workspace seed")
        raise typer.Exit(code=1)
    return workspace


def _pack_and_brands(
    container: Container, workspace: Workspace
) -> tuple[DomainPack, list[BrandProfile]]:
    """The channels to write for, and the voices to write in."""
    try:
        config = container.policy_loader.load_workspace(workspace.slug)
        pack = container.packs.load(config.pack)
        brands = [
            container.policy_loader.load_brand_profile(key, workspace.id)
            for key in config.brand_profiles
        ]
    except (PolicyLoadError, PackNotFoundError, PackLoadError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if not brands:
        console.print(
            f"[red]Workspace {workspace.slug!r} declares no brand profiles.[/red] "
            "Generation needs a voice to write in."
        )
        raise typer.Exit(code=1)
    return pack, brands


@app.command("run")
def run(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    limit: int = typer.Option(None, "--limit", "-n", help="Max selected stories to write for."),
    channel: list[str] = typer.Option(
        None, "--channel", "-c", help="Only these channels. Repeatable."
    ),
    story_id: str = typer.Option(None, "--story", help="Write for one selected story only."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Generate and report without storing anything."
    ),
) -> None:
    """Write posts for the selected stories."""
    container = build_container()
    workspace = _require_workspace(container, slug)
    pack, brands = _pack_and_brands(container, workspace)
    settings = container.settings

    stage = GenerateStage(
        llm=container.llm,
        pack=pack,
        brand_profiles=brands,
        model=settings.active_model,
        max_stories=limit or settings.generate_max_stories,
        channels=list(channel) if channel else None,
        only_story=story_id,
        max_tokens=settings.generate_max_tokens,
        max_cost_usd=settings.max_cost_usd_per_run,
    )
    runner = PipelineRunner(uow=container.unit_of_work(), clock=container.clock, settings=settings)

    console.print(
        f"Provider [cyan]{settings.llm_provider}[/cyan], model [cyan]{settings.active_model}[/cyan]"
        f"  [dim]voices: {', '.join(brand.key for brand in brands)}[/dim]"
    )

    try:
        ledger = runner.run(
            workspace_id=workspace.id,
            pipeline="generate",
            stages=[stage],
            trigger="cli",
            dry_run=dry_run,
        )
    except Exception as exc:
        console.print(f"[red]Generation failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    with container.unit_of_work() as uow:
        records = uow.runs.list_stages(workspace.id, ledger.id)
    meta: dict[str, Any] = next(
        (record.meta for record in records if record.stage == "generate"), {}
    )

    if not meta or meta.get("skip_reason"):
        console.print(
            f"[yellow]Nothing to generate:[/yellow] {meta.get('skip_reason', 'no stories')}"
        )
    else:
        _print_outcome(meta)

    console.print(
        f"[green]Run {ledger.status.value}:[/green] {ledger.id} "
        f"({ledger.duration_seconds:.1f}s, {ledger.usage.output_tokens} output tokens, "
        f"${ledger.cost_usd:.4f})"
    )
    if dry_run:
        console.print("[dim]Dry run -- nothing was stored.[/dim]")


def _print_outcome(meta: dict[str, Any]) -> None:
    console.print(
        f"Wrote [green]{meta.get('generated', 0)}[/green] asset(s) for "
        f"{meta.get('stories', 0)} story(ies) across {len(meta.get('targets', []))} target(s) "
        f"[dim](prompt {meta.get('prompt_version')})[/dim]"
    )
    if meta.get("halted"):
        console.print(f"[yellow]Budget:[/yellow] {meta['halted']}")

    failures = meta.get("failures", {})
    if not failures:
        return
    table = Table(title="Not written")
    table.add_column("Reason")
    table.add_column("Assets", justify="right")
    for code, count in sorted(failures.items(), key=lambda item: -item[1]):
        table.add_row(_describe(code), str(count))
    console.print(table)


@app.command("list")
def list_assets(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    limit: int = typer.Option(20, "--limit", "-n", help="How many to show."),
) -> None:
    """Show recently generated assets."""
    container = build_container()
    workspace = _require_workspace(container, slug)

    with container.unit_of_work() as uow:
        assets = uow.assets.list_recent(workspace.id, limit=limit)
        stories = {
            asset.story_id: uow.stories.get(workspace.id, asset.story_id) for asset in assets
        }

    if not assets:
        console.print("[yellow]Nothing generated yet.[/yellow] Run: engine generate run")
        return

    table = Table(title=f"Generated assets for {workspace.slug}")
    table.add_column("Asset", style="dim", overflow="fold")
    table.add_column("Channel")
    table.add_column("Lang")
    table.add_column("State")
    table.add_column("Story", style="cyan", overflow="fold")
    for asset in assets:
        story = stories.get(asset.story_id)
        table.add_row(
            asset.id,
            asset.channel_key or "-",
            str(asset.language),
            asset.state.value,
            (story.canonical_headline[:45] if story else asset.story_id),
        )
    console.print(table)


@app.command("show")
def show_asset(
    asset_id: str = typer.Argument(..., help="The asset to print."),
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    history: bool = typer.Option(False, "--history", help="List every revision, not just current."),
) -> None:
    """Print one asset's current text, and optionally its revision history."""
    container = build_container()
    workspace = _require_workspace(container, slug)

    with container.unit_of_work() as uow:
        asset = uow.assets.get(workspace.id, asset_id)
        if asset is None:
            console.print(f"[red]Asset {asset_id!r} not found.[/red]")
            raise typer.Exit(code=1)
        current = (
            uow.assets.get_revision(workspace.id, asset.current_revision_id)
            if asset.current_revision_id
            else None
        )
        revisions = uow.assets.list_revisions(workspace.id, asset.id) if history else []
        story = uow.stories.get(workspace.id, asset.story_id)

    header = (
        f"{asset.channel_key or 'no channel'} / {asset.language} / {asset.product_key}"
        f"  [dim]{asset.state.value}, voice {asset.brand_profile_key}[/dim]"
    )
    console.print(Panel(current.body if current else "[dim]no revision[/dim]", title=header))

    if story:
        console.print(f"[dim]Story:[/dim] {story.canonical_headline}")
        console.print(f"[dim]Source:[/dim] {story.primary_url or '-'}")
    hashtags = asset.meta.get(HASHTAGS_KEY) or []
    if hashtags:
        console.print("[dim]Hashtags:[/dim] " + " ".join(f"#{tag}" for tag in hashtags))
    if asset.meta.get(HOOK_KEY):
        console.print(f"[dim]Hook:[/dim] {asset.meta[HOOK_KEY]}")
    console.print(
        f"[dim]Characters:[/dim] {len(current.body) if current else 0}"
        f"   [dim]Model:[/dim] {asset.meta.get('model', '-')}"
        f"   [dim]Cost:[/dim] ${float(asset.meta.get(COST_KEY, 0)):.6f}"
    )

    if not history:
        return
    table = Table(title="Revisions")
    table.add_column("#", justify="right")
    table.add_column("Origin")
    table.add_column("Prompt")
    table.add_column("Created (UTC)")
    table.add_column("Chars", justify="right")
    for revision in revisions:
        table.add_row(
            str(revision.revision_number),
            revision.origin.value,
            revision.prompt_version or "-",
            revision.created_at.strftime("%Y-%m-%d %H:%M"),
            str(len(revision.body)),
        )
    console.print(table)
