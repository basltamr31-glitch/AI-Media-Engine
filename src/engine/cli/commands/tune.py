"""Tuning commands: what the engagement data says about the ranking.

``tune weights`` closes the loop the architecture was built around -- published
content, measured outcomes, and back into the ranking that chose it. It is
strictly a **proposal**: it prints a scoring profile and the evidence behind it,
and a human decides whether to paste it into the workspace config.

That restraint is the design. Editorial judgement is the durable asset here,
and handing it to an unsupervised statistic over a few dozen posts would trade
the most valuable thing in the system for arithmetic. What the tuner is for is
making the judgement *informed*: an editor who can see that corroboration
correlates with engagement and recency does not is making a better decision
than one who is guessing, whether or not they take the suggested numbers.
"""

from __future__ import annotations

from collections import defaultdict

import typer
from rich.console import Console
from rich.table import Table

from engine.adapters.config.policy_loader import PolicyLoadError
from engine.cli.container import Container, build_container
from engine.core.domain.content import PublicationPerformance
from engine.core.domain.tuning import (
    DEFAULT_DAMPING,
    MIN_SAMPLES,
    Observation,
    WeightProposal,
    propose_weights,
)
from engine.core.domain.workspace import Workspace
from engine.core.ports.packs import PackLoadError, PackNotFoundError

console = Console()
app = typer.Typer(help="What measured engagement says about the ranking.")

#: How many publications to consider. Generous: the limit exists to bound a
#: query, not to window the analysis, and a workspace with more than this much
#: history has a better problem than this command.
MAX_PUBLICATIONS = 1000


def _require_workspace(container: Container, slug: str | None) -> Workspace:
    target = slug or container.settings.default_workspace_slug
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target)
    if workspace is None:
        console.print(f"[red]Workspace {target!r} not found.[/red] Run: engine workspace seed")
        raise typer.Exit(code=1)
    return workspace


@app.command("weights")
def weights(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    metric: str = typer.Option(
        None, "--metric", help="Engagement metric to fit against. Default: the most recorded one."
    ),
    min_samples: int = typer.Option(
        MIN_SAMPLES, "--min-samples", help="Refuse to propose below this many measured stories."
    ),
    damping: float = typer.Option(
        DEFAULT_DAMPING, "--damping", help="0 keeps current weights, 1 follows the data fully."
    ),
) -> None:
    """Propose ranking weights from what published content actually achieved."""
    container = build_container()
    workspace = _require_workspace(container, slug)
    current = _current_weights(container, workspace)

    observations, chosen_metric = _observations(container, workspace, metric)
    if metric and not observations:
        console.print(f"[yellow]No stories carry the metric {metric!r}.[/yellow]")

    proposal = propose_weights(
        observations,
        current,
        metric=chosen_metric,
        min_samples=min_samples,
        damping=damping,
    )
    _print(proposal, workspace)


def _current_weights(container: Container, workspace: Workspace) -> dict[str, float]:
    try:
        config = container.policy_loader.load_workspace(workspace.slug)
        pack = container.packs.load(config.pack)
    except (PolicyLoadError, PackNotFoundError, PackLoadError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    profile = config.editorial.scoring
    # An empty profile means "inherit the pack's defaults", and those are what
    # the scorer actually used -- so they are what a proposal must move from.
    return (
        dict(profile.weights)
        if not profile.is_empty
        else dict(pack.default_scoring_profile.weights)
    )


def _observations(
    container: Container, workspace: Workspace, metric: str | None
) -> tuple[list[Observation], str]:
    """Assemble the feature matrix: signal values against measured engagement.

    Grouped **by story, not by publication**. One story published to four
    channels is one editorial decision that happened to be distributed four
    ways; counting it four times would let a single well-chosen story dominate
    the fit while looking like four independent confirmations.
    """
    with container.unit_of_work() as uow:
        performances = uow.metrics.performance(workspace.id, limit=MAX_PUBLICATIONS)
        if not performances:
            return [], metric or "engagement"

        chosen = metric or _most_recorded(performances)

        outcomes: dict[str, float] = defaultdict(float)
        signals: dict[str, dict[str, float]] = {}
        for performance in performances:
            if chosen not in performance.metrics:
                continue
            asset = uow.assets.get(workspace.id, performance.asset_id)
            if asset is None:
                continue
            if asset.story_id not in signals:
                score = uow.scores.latest_for_story(workspace.id, asset.story_id)
                if score is None or not score.raw:
                    # Scored before raw values were kept, or never scored. It
                    # cannot contribute a feature row, and guessing zeros would
                    # be inventing training data.
                    continue
                signals[asset.story_id] = dict(score.raw)
            outcomes[asset.story_id] += performance.metrics[chosen]

    return (
        [
            Observation(story_id=story_id, signals=signals[story_id], outcome=outcome)
            for story_id, outcome in outcomes.items()
            if story_id in signals
        ],
        chosen,
    )


def _most_recorded(performances: list[PublicationPerformance]) -> str:
    """The metric with the most observations, so the default fits the best data."""
    counts: dict[str, int] = defaultdict(int)
    for performance in performances:
        for name in performance.metrics:
            counts[name] += 1
    if not counts:
        return "engagement"
    return max(counts, key=lambda name: counts[name])


def _print(proposal: WeightProposal, workspace: Workspace) -> None:
    console.print(
        f"Fitting [cyan]{proposal.metric}[/cyan] over "
        f"{proposal.samples} measured story/stories in {workspace.slug}"
    )

    if not proposal.signals:
        console.print(f"[yellow]{proposal.reason}[/yellow]")
        console.print("[dim]Record some outcomes first: engine metrics add[/dim]")
        return

    table = Table(title="Ranking signals vs engagement")
    table.add_column("Signal", style="cyan")
    table.add_column("Correlation", justify="right")
    table.add_column("Weight now", justify="right")
    table.add_column("Proposed", justify="right")
    table.add_column("", justify="center")
    arrows = {"up": "[green]^[/green]", "down": "[red]v[/red]", "hold": "[dim]=[/dim]"}
    for signal in proposal.signals:
        table.add_row(
            signal.key,
            "[dim]never varied[/dim]" if not signal.varied else f"{signal.correlation:+.3f}",
            f"{signal.current_weight:.4f}",
            f"{signal.proposed_weight:.4f}",
            arrows[signal.direction] if proposal.is_actionable else "",
        )
    console.print(table)

    if not proposal.is_actionable:
        console.print(f"[yellow]No proposal:[/yellow] {proposal.reason}")
        return

    strongest = proposal.strongest
    if strongest is not None:
        console.print(
            f"Most associated with {proposal.metric}: "
            f"[cyan]{strongest.key}[/cyan] ({strongest.correlation:+.3f})"
        )

    console.print("\n[dim]To adopt, put this in the workspace's editorial.scoring:[/dim]")
    console.print("[bold]weights:[/bold]")
    for signal in proposal.signals:
        console.print(f"  {signal.key}: {signal.proposed_weight}")
    console.print(
        "\n[dim]Correlation is not causation and the sample is small. "
        "These are an input to your judgement, not a replacement for it.[/dim]"
    )
