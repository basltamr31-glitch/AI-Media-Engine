"""CLI entry point.

The CLI is one entry point among several planned (an HTTP API and a scheduled
worker follow). It contains no business logic: it parses arguments, builds the
container and delegates. Keeping it that thin is what allows the API to be added
later without reimplementing anything.
"""

from __future__ import annotations

import typer
from rich.console import Console

from engine import __version__
from engine.cli.commands import db, diagnostics, ingest, pipeline, sources, workspace

console = Console()

app = typer.Typer(
    name="engine",
    help="AI Media Engine -- multi-source ingestion, editorial ranking, multi-channel content.",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(db.app, name="db")
app.add_typer(workspace.app, name="workspace")
app.add_typer(sources.app, name="sources")
app.add_typer(ingest.app, name="ingest")
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
