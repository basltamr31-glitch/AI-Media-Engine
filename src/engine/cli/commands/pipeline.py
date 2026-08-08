"""Pipeline commands.

``pipeline run`` executes the whole daily pipeline: ingest, parse, resolve,
enrich, evidence, score, select, generate. Each phase appended to one list and
changed nothing else here -- the runner, ledger and cost ceiling written in
Phase 0 have accommodated eight stages without modification, which is the
return on building the orchestrator before anything to orchestrate.

The individual ``engine <stage> run`` commands remain, and are what to reach
for when debugging one step. This is the command a scheduler calls.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from engine.adapters.config.policy_loader import PolicyLoadError
from engine.adapters.config.schema import WorkspaceConfig
from engine.cli.container import Container, build_container
from engine.core.domain.content import BrandProfile
from engine.core.domain.pack import DomainPack
from engine.core.domain.workspace import Workspace
from engine.core.ports.packs import PackLoadError, PackNotFoundError
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.base import Stage
from engine.pipeline.stages.enrich import EnrichStage
from engine.pipeline.stages.evidence import EvidenceStage
from engine.pipeline.stages.generate import GenerateStage
from engine.pipeline.stages.ingest import IngestStage
from engine.pipeline.stages.noop import NoOpStage
from engine.pipeline.stages.parse import ParseStage
from engine.pipeline.stages.resolve import ResolveStage
from engine.pipeline.stages.score import ScoreStage
from engine.pipeline.stages.select import SelectStage

console = Console()
app = typer.Typer(help="Pipeline execution and run history.")


def build_daily_stages(container: Container, workspace: Workspace) -> list[Stage]:
    """Assemble the daily content pipeline, in the order it must run.

    The single place the stage order is declared. Each stage consumes what the
    previous one produced, and every one of them is a no-op when there is
    nothing new -- which is what makes running the whole thing on a timer safe
    rather than wasteful.

    The pipeline deliberately **stops at generation**. Rendering writes a dated
    edition, and a scheduled run that produced an edition every morning would
    imply the day's content was finished when in fact nothing had been reviewed.
    Review is a human gate (ADR 0018): the unattended part ends by filling the
    queue, and ``engine render run`` is invoked once a person has emptied it.

    Raises:
        typer.Exit: If the workspace's configuration cannot be loaded. A daily
            run with a broken policy must fail loudly at assembly rather than
            silently skip the stages that needed it.
    """
    settings = container.settings
    config, pack, brands = _configuration(container, workspace)

    return [
        IngestStage(
            fetchers=container.fetchers,
            archive=container.raw_archive,
            max_documents=settings.ingest_max_documents,
        ),
        ParseStage(
            parsers=container.parsers,
            max_documents=settings.parse_max_documents,
        ),
        ResolveStage(
            max_candidates=settings.resolve_max_candidates,
            window_days=settings.resolve_window_days,
            similarity_threshold=settings.resolve_similarity_threshold,
        ),
        EnrichStage(
            http=container.http,
            extractor=container.extractor,
            max_stories=settings.enrich_max_stories,
            max_attempts=settings.enrich_max_attempts,
        ),
        EvidenceStage(
            max_stories=settings.evidence_max_stories,
            window_days=settings.resolve_window_days,
        ),
        ScoreStage(
            computers=container.signal_computers,
            pack=pack,
            policy=config.editorial,
            max_stories=settings.score_max_stories,
            window_days=settings.resolve_window_days,
        ),
        SelectStage(
            policy=config.editorial,
            window_hours=settings.select_window_hours,
            candidate_window_days=settings.resolve_window_days,
        ),
        GenerateStage(
            llm=container.llm,
            pack=pack,
            brand_profiles=brands,
            model=settings.active_model,
            max_stories=settings.generate_max_stories,
            max_tokens=settings.generate_max_tokens,
            max_cost_usd=settings.max_cost_usd_per_run,
        ),
    ]


def _configuration(
    container: Container, workspace: Workspace
) -> tuple[WorkspaceConfig, DomainPack, list[BrandProfile]]:
    """Everything the daily pipeline needs from configuration, loaded once."""
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
    return config, pack, brands


@app.command("run")
def run(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Run without side effects."),
    smoke: bool = typer.Option(
        False, "--smoke", help="Include a no-op stage to exercise the orchestrator."
    ),
) -> None:
    """Execute the daily content pipeline."""
    container = build_container()
    target_slug = slug or container.settings.default_workspace_slug

    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target_slug)
    if workspace is None:
        console.print(f"[red]Workspace {target_slug!r} not found.[/red] Run: engine workspace seed")
        raise typer.Exit(code=1)

    stages: list[Stage] = (
        [NoOpStage("smoke")] if smoke else build_daily_stages(container, workspace)
    )

    runner = PipelineRunner(
        uow=container.unit_of_work(),
        clock=container.clock,
        settings=container.settings,
    )
    ledger = runner.run(
        workspace_id=workspace.id,
        pipeline="daily_content",
        stages=stages,
        trigger="cli",
        dry_run=dry_run,
    )

    console.print(
        f"[green]Run {ledger.status.value}:[/green] {ledger.id} "
        f"({len(stages)} stage(s), {ledger.duration_seconds:.3f}s, "
        f"${ledger.cost_usd:.4f})"
    )
    console.print(f"[dim]Breakdown: engine pipeline stages {ledger.id}[/dim]")


@app.command("runs")
def list_runs(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    limit: int = typer.Option(10, "--limit", "-n", help="How many runs to show."),
) -> None:
    """Show recent pipeline runs with their cost and duration."""
    container = build_container()
    target_slug = slug or container.settings.default_workspace_slug

    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target_slug)
        if workspace is None:
            console.print(f"[red]Workspace {target_slug!r} not found.[/red]")
            raise typer.Exit(code=1)
        runs = uow.runs.list_recent(workspace.id, limit=limit)

    if not runs:
        console.print("[yellow]No runs recorded yet.[/yellow]")
        return

    table = Table(title=f"Recent runs for {workspace.slug}")
    table.add_column("Started (UTC)")
    table.add_column("Pipeline", style="cyan")
    table.add_column("Status")
    table.add_column("Duration", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("ID", style="dim")
    for entry in runs:
        duration = entry.duration_seconds
        table.add_row(
            entry.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            entry.pipeline,
            entry.status.value,
            f"{duration:.2f}s" if duration is not None else "-",
            f"${entry.cost_usd:.4f}",
            entry.id,
        )
    console.print(table)


@app.command("stages")
def show_stages(
    run_id: str = typer.Argument(..., help="Run identifier."),
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
) -> None:
    """Show the per-stage breakdown of one run."""
    container = build_container()
    target_slug = slug or container.settings.default_workspace_slug

    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target_slug)
        if workspace is None:
            console.print(f"[red]Workspace {target_slug!r} not found.[/red]")
            raise typer.Exit(code=1)
        stages = uow.runs.list_stages(workspace.id, run_id)

    if not stages:
        console.print(f"[yellow]No stages recorded for run {run_id}.[/yellow]")
        return

    table = Table(title=f"Stages for {run_id}")
    table.add_column("#", justify="right")
    table.add_column("Stage", style="cyan")
    table.add_column("Status")
    table.add_column("In", justify="right")
    table.add_column("Out", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Cost", justify="right")
    for record in stages:
        duration = record.duration_seconds
        table.add_row(
            str(record.sequence),
            record.stage,
            record.status.value,
            str(record.items_in),
            str(record.items_out),
            f"{duration:.3f}s" if duration is not None else "-",
            f"${record.cost_usd:.4f}",
        )
    console.print(table)
