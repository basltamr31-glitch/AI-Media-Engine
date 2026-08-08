"""Evaluation commands.

``eval run`` generates the golden set and scores it. ``--save-baseline``
records the result; every later run is compared against it, which is what turns
a score into a *regression signal* -- a number on its own tells you nothing
without the number it used to be.

The checks are algorithmic and cost nothing, so this is meant to be run after
every prompt change rather than saved for a release.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from engine.adapters.evaluation.golden import GoldenSet, GoldenSetError
from engine.adapters.evaluation.runner import EvalReport, EvalRunner
from engine.cli.container import Container, build_container

console = Console()
app = typer.Typer(help="Score generation against the golden set.")

#: Where the baseline lives. Under the golden set rather than in `data/`
#: because it is a checked-in expectation, not machine-local state.
BASELINE_NAME = "baseline.json"

#: How far a score may fall before it is called a regression. Small but not
#: zero: generation is not deterministic, and a suite that cries wolf on
#: sampling noise is a suite people learn to ignore.
DEFAULT_TOLERANCE = 0.05


def _golden(container: Container) -> GoldenSet:
    return GoldenSet(container.resolve_path(Path("evals") / "golden"))


def _baseline_path(golden: GoldenSet) -> Path:
    return golden.directory.parent / BASELINE_NAME


@app.command("list")
def list_cases() -> None:
    """Show the golden cases."""
    container = build_container()
    golden = _golden(container)
    try:
        cases = golden.load_all()
    except GoldenSetError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title=f"Golden set ({golden.directory})")
    table.add_column("Case", style="cyan")
    table.add_column("Lang")
    table.add_column("Channel")
    table.add_column("Limit", justify="right")
    table.add_column("Source chars", justify="right")
    for case in cases:
        table.add_row(
            case.key,
            case.language,
            case.channel_key,
            str(case.max_characters),
            str(len(case.source_text)),
        )
    console.print(table)


@app.command("run")
def run(
    case_key: str = typer.Option(None, "--case", help="Run one case only."),
    save_baseline: bool = typer.Option(
        False, "--save-baseline", help="Record this run as the expectation."
    ),
    tolerance: float = typer.Option(
        DEFAULT_TOLERANCE, "--tolerance", help="Allowed drop before a regression is reported."
    ),
    repeat: int = typer.Option(
        1, "--repeat", min=1, max=9, help="Sample each case N times and keep the median."
    ),
    min_score: float = typer.Option(
        None,
        "--min-score",
        min=0.0,
        max=1.0,
        help="Absolute floor. Fails below it regardless of the baseline.",
    ),
) -> None:
    """Generate the golden set and score it against the checks."""
    container = build_container()
    golden = _golden(container)
    settings = container.settings

    try:
        cases = golden.load_all()
    except GoldenSetError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if case_key:
        cases = [case for case in cases if case.key == case_key]
        if not cases:
            console.print(f"[red]No golden case {case_key!r}.[/red] Run: engine eval list")
            raise typer.Exit(code=1)

    console.print(
        f"Provider [cyan]{settings.llm_provider}[/cyan], model "
        f"[cyan]{settings.active_model}[/cyan], {len(cases)} case(s)"
    )

    if save_baseline and repeat == 1:
        # A baseline is the number everything later is judged against, and a
        # single sample of a non-deterministic model is not a number, it is a
        # coin toss. Observed spread on one case across two runs: 0.55 to 0.98.
        console.print(
            "[yellow]Recording a baseline from one sample per case.[/yellow] "
            "Generation is non-deterministic -- prefer --repeat 3 or more."
        )

    runner = EvalRunner(
        llm=container.llm,
        model=settings.active_model,
        max_tokens=settings.generate_max_tokens,
        repeat=repeat,
    )
    report = runner.run(cases)

    _print_report(report)

    baseline_path = _baseline_path(golden)
    if save_baseline:
        baseline_path.write_text(json.dumps(_as_baseline(report), indent=2), encoding="utf-8")
        console.print(f"[green]Baseline saved:[/green] {baseline_path}")
        raise typer.Exit(code=0 if report.passed else 1)

    regressed = _compare(report, baseline_path, tolerance)

    # An absolute floor as well as the baseline comparison, because the two
    # catch different things. The baseline catches a drop; the floor catches a
    # slow slide that never drops far enough in one step to look like one, and
    # a baseline that was re-saved after each small decline.
    below_floor = min_score is not None and report.score < min_score
    if below_floor:
        console.print(f"[red]Below the floor:[/red] {report.score:.3f} < {min_score:.3f}")

    raise typer.Exit(code=1 if (regressed or below_floor or not report.passed) else 0)


def _print_report(report: EvalReport) -> None:
    table = Table(title=f"Golden set ({report.model}, prompt {report.prompt_version})")
    table.add_column("Case", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("Failed checks", overflow="fold")
    for outcome in report.outcomes:
        if outcome.error:
            detail = f"[red]{outcome.error}[/red]"
        elif outcome.failures:
            detail = "; ".join(f"{check.name} ({check.detail})" for check in outcome.failures)
        else:
            detail = "[green]all passed[/green]"
        colour = "green" if outcome.passed else "red"
        table.add_row(outcome.key, f"[{colour}]{outcome.score:.3f}[/{colour}]", detail)
    console.print(table)

    verdict = "[green]PASS[/green]" if report.passed else "[red]FAIL[/red]"
    sampling = f", median of {report.repeat}" if report.repeat > 1 else ""
    console.print(
        f"{verdict}  overall {report.score:.3f}{sampling}  "
        f"[dim]{sum(o.output_tokens for o in report.outcomes)} output tokens, "
        f"${report.cost_usd:.4f}[/dim]"
    )


def _as_baseline(report: EvalReport) -> dict[str, Any]:
    return {
        "model": report.model,
        "prompt_version": report.prompt_version,
        # Recorded so a baseline taken from one noisy sample is not silently
        # compared against a careful one, or the other way round.
        "repeat": report.repeat,
        "overall": round(report.score, 4),
        "cases": {outcome.key: round(outcome.score, 4) for outcome in report.outcomes},
    }


def _compare(report: EvalReport, baseline_path: Path, tolerance: float) -> bool:
    """Report movement against the recorded expectation. True if worse."""
    if not baseline_path.is_file():
        console.print("[yellow]No baseline recorded.[/yellow] Run: engine eval run --save-baseline")
        return False

    try:
        baseline: dict[str, Any] = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        console.print(f"[red]Cannot read baseline:[/red] {exc}")
        return False

    recorded: dict[str, float] = baseline.get("cases", {})
    if baseline.get("prompt_version") != report.prompt_version:
        # Not a failure: a prompt change is *expected* to move scores. Saying
        # so keeps the comparison honest rather than alarming.
        console.print(
            f"[dim]Baseline was recorded on prompt {baseline.get('prompt_version')}, "
            f"this run is {report.prompt_version}.[/dim]"
        )
    if baseline.get("model") != report.model:
        console.print(
            f"[dim]Baseline model was {baseline.get('model')}, this run is {report.model}.[/dim]"
        )
    if baseline.get("repeat", 1) != report.repeat:
        console.print(
            f"[dim]Baseline sampled {baseline.get('repeat', 1)}x per case, "
            f"this run {report.repeat}x -- some of any difference is sampling noise.[/dim]"
        )

    regressions: list[tuple[str, float, float]] = []
    for outcome in report.outcomes:
        was = recorded.get(outcome.key)
        if was is not None and outcome.score < was - tolerance:
            regressions.append((outcome.key, was, outcome.score))

    if not regressions:
        console.print(
            f"[green]No regression[/green] against baseline "
            f"(overall {baseline.get('overall', 0):.3f} -> {report.score:.3f})"
        )
        return False

    table = Table(title="Regressions")
    table.add_column("Case", style="cyan")
    table.add_column("Was", justify="right")
    table.add_column("Now", justify="right", style="red")
    for key, was, now in regressions:
        table.add_row(key, f"{was:.3f}", f"{now:.3f}")
    console.print(table)
    return True
