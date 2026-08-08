"""CLI entry point.

The CLI is one entry point among several planned (an HTTP API and a scheduled
worker follow). It contains no business logic: it parses arguments, builds the
container and delegates. Keeping it that thin is what allows the API to be added
later without reimplementing anything.
"""

from __future__ import annotations

import sys

import typer
from rich.console import Console

from engine import __version__
from engine.cli.commands import (
    cost,
    db,
    diagnostics,
    enrich,
    evals,
    evidence,
    export,
    generate,
    identity,
    ingest,
    llm,
    metrics,
    pack,
    parse,
    pipeline,
    plugins,
    publish,
    render,
    review,
    schedule,
    score,
    select,
    sources,
    stories,
    tune,
    workspace,
)


def _use_utf8_console() -> None:
    """Make the console able to print what the engine writes.

    Windows terminals still default to a legacy code page, and cp1252 cannot
    encode Arabic at all: printing a generated post raised ``UnicodeEncodeError``
    rather than showing it. Arabic is the wedge this product is built on, so a
    CLI that cannot display it is not usable for the review step it exists to
    support.

    ``errors="replace"`` rather than ``strict``: a terminal that genuinely
    cannot render a glyph should show a placeholder, not abort the command that
    was about to tell someone what their post says.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


_use_utf8_console()

console = Console()

app = typer.Typer(
    name="engine",
    help="AI Media Engine -- multi-source ingestion, editorial ranking, multi-channel content.",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(db.app, name="db")
app.add_typer(identity.account_app, name="account")
app.add_typer(identity.signals_app, name="signals")
app.add_typer(workspace.app, name="workspace")
app.add_typer(pack.app, name="pack")
app.add_typer(plugins.app, name="plugins")
app.add_typer(sources.app, name="sources")
app.add_typer(ingest.app, name="ingest")
app.add_typer(parse.app, name="parse")
app.add_typer(stories.resolve_app, name="resolve")
app.add_typer(enrich.app, name="enrich")
app.add_typer(evidence.app, name="evidence")
app.add_typer(score.app, name="score")
app.add_typer(select.app, name="select")
app.add_typer(generate.app, name="generate")
app.add_typer(cost.app, name="cost")
app.add_typer(evals.app, name="eval")
app.add_typer(render.app, name="render")
app.add_typer(review.app, name="review")
app.add_typer(schedule.app, name="schedule")
app.add_typer(tune.app, name="tune")
app.add_typer(export.app, name="export")
app.add_typer(publish.app, name="publish")
app.add_typer(metrics.app, name="metrics")
app.add_typer(stories.stories_app, name="stories")
app.add_typer(llm.app, name="llm")
app.add_typer(pipeline.app, name="pipeline")
app.add_typer(diagnostics.app, name="config", help="Configuration inspection.")

# `doctor` is promoted to the top level: it is the first thing anyone runs.
app.command("doctor")(diagnostics.doctor)


@app.command("version")
def version() -> None:
    """Print the engine version."""
    console.print(f"ai-media-engine [cyan]{__version__}[/cyan]")


if __name__ == "__main__":  # pragma: no cover
    app()
