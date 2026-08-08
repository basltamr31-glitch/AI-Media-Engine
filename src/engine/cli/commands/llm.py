"""LLM commands: prove the configured model actually works.

``engine llm check`` is the counterpart to ``engine doctor`` for generation. It
answers one question -- *does the configured provider return output that
satisfies a schema?* -- and answers it by making a real call, because a mocked
answer to that question is worth nothing.

With ``--story`` it runs the same probe over a real story's extracted text, at
a realistic prompt size. That distinction matters: a model that handles a
two-sentence toy prompt can still truncate, stall or drift on eight thousand
tokens of article, and the point of checking is to find that out here rather
than in a pipeline run.

This is a diagnostic, not a pipeline stage. It writes nothing, produces no
content asset and belongs to no run -- generation proper arrives with its own
stage, prompts and persistence.
"""

from __future__ import annotations

import time

import typer
from pydantic import BaseModel, Field
from rich.console import Console
from rich.table import Table

from engine.cli.container import Container, build_container
from engine.core.ports.llm import LLMError, LLMRequest

console = Console()
app = typer.Typer(help="Check and inspect the configured language model.")

#: A neutral paragraph for the no-story case. Deliberately factual and dull:
#: the probe is testing the plumbing, and creative latitude would make a wrong
#: answer hard to distinguish from a right one.
_SAMPLE_TEXT = (
    "A research group has released an open-weights language model with eight "
    "billion parameters. The release includes the training recipe and an "
    "evaluation harness. The team reports the model runs on a single consumer "
    "GPU and publishes results on six public benchmarks."
)

_SYSTEM = (
    "You are a precise editorial assistant. You extract only what the supplied "
    "text actually states. You never add facts that are not present in it."
)


class SourceCheck(BaseModel):
    """What the probe asks the model to return.

    Small, but not trivial: a string, a list and a boolean together exercise
    the parts of schema-constrained decoding that actually break.
    """

    title: str = Field(description="A short factual title for the text, under twelve words.")
    key_points: list[str] = Field(description="Two to four factual claims stated in the text.")
    mentions_ai: bool = Field(description="True if the text concerns AI or machine learning.")


@app.command("check")
def check(
    story_id: str = typer.Option(
        None, "--story", help="Probe against this story's extracted text instead of a sample."
    ),
    corpus: bool = typer.Option(
        False, "--corpus", help="Probe against the most recently selected story."
    ),
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    max_chars: int = typer.Option(
        6000, "--max-chars", help="Truncate the story text to this many characters."
    ),
) -> None:
    """Make one real call and verify the response satisfies a schema."""
    container = build_container()
    settings = container.settings
    model = settings.active_model

    text, label = _probe_text(
        container, story_id=story_id, corpus=corpus, slug=slug, max_chars=max_chars
    )

    console.print(
        f"Provider [cyan]{settings.llm_provider}[/cyan], model [cyan]{model}[/cyan], "
        f"input [dim]{label} ({len(text)} chars)[/dim]"
    )
    if settings.llm_provider == "ollama":
        console.print(f"[dim]Talking to {settings.ollama_base_url} -- local, no cost.[/dim]")

    request = LLMRequest(
        system=_SYSTEM,
        prompt=f"Text:\n\n{text}",
        model=model,
        max_tokens=1024,
    )

    started = time.monotonic()
    try:
        result = container.llm.generate_structured(request, SourceCheck)
    except LLMError as exc:
        console.print(f"[red]Check failed[/red] ({exc.code}): {exc}")
        raise typer.Exit(code=1) from exc
    elapsed = time.monotonic() - started

    console.print("[green]Schema satisfied.[/green]")
    console.print(f"  title       {result.value.title}")
    for point in result.value.key_points:
        console.print(f"  - {point}")
    console.print(f"  mentions_ai {result.value.mentions_ai}")

    table = Table(title="Call")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    table.add_row("model", result.model)
    table.add_row("input tokens", str(result.usage.input_tokens))
    table.add_row("output tokens", str(result.usage.output_tokens))
    if result.usage.cache_read_tokens or result.usage.cache_write_tokens:
        table.add_row("cache read tokens", str(result.usage.cache_read_tokens))
        table.add_row("cache write tokens", str(result.usage.cache_write_tokens))
    table.add_row("cost", f"${result.cost_usd:.6f}")
    table.add_row("elapsed", f"{elapsed:.1f}s")
    table.add_row("stop reason", result.stop_reason or "-")
    console.print(table)


def _probe_text(
    container: Container,
    *,
    story_id: str | None,
    corpus: bool,
    slug: str | None,
    max_chars: int,
) -> tuple[str, str]:
    """The text to probe with, and a label naming where it came from."""
    if story_id is None and not corpus:
        return _SAMPLE_TEXT, "built-in sample"

    target = slug or container.settings.default_workspace_slug
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target)
        if workspace is None:
            console.print(f"[red]Workspace {target!r} not found.[/red] Run: engine workspace seed")
            raise typer.Exit(code=1)
        if story_id is not None:
            story = uow.stories.get(workspace.id, story_id)
        else:
            selected = uow.stories.list_selected(workspace.id, limit=1)
            story = selected[0] if selected else None

    if story is None:
        where = f"Story {story_id!r}" if story_id else "No selected story"
        console.print(f"[red]{where} not found.[/red] Run: engine select run")
        raise typer.Exit(code=1)
    if story.enrichment is None or not story.enrichment.text:
        console.print(
            f"[red]{story.canonical_headline[:60]!r} has no extracted text.[/red] "
            "Run: engine enrich run"
        )
        raise typer.Exit(code=1)
    return story.enrichment.text[:max_chars], story.canonical_headline[:50]
