"""End-to-end CLI tests for ``engine llm check``.

The command's job is to make a *real* call, so what is testable here is
everything around that call: which text it picks, what it reports, and whether
a failure is legible. Whether a model can actually satisfy the schema is
settled by running one, not by asserting against a double.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from engine.cli.commands.llm import SourceCheck
from engine.cli.container import Container, build_container
from engine.core.domain.enums import StoryStatus
from engine.core.domain.story import EnrichedSource, Story
from engine.settings import get_settings
from tests.fakes import FakeLLM

pytestmark = pytest.mark.integration

runner = CliRunner()

ARTICLE = "The laboratory published an eight-billion-parameter model under an open licence."


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("ENGINE_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'llm.db'}")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENGINE_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("ENGINE_GOOGLE_CREDENTIALS_FILE", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("ENGINE_GOOGLE_TOKEN_FILE", str(tmp_path / "token.json"))
    monkeypatch.setenv("COLUMNS", "240")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def seeded(cli_env: Path) -> Path:
    from engine.cli.main import app

    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
    assert runner.invoke(app, ["workspace", "seed"]).exit_code == 0
    return cli_env


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeLLM) -> FakeLLM:
    """Swap the container's provider without touching any command's code."""
    monkeypatch.setattr(Container, "llm", property(lambda self: fake))
    return fake


def answering() -> FakeLLM:
    return FakeLLM(
        value=SourceCheck(title="An open model", key_points=["eight billion"], mentions_ai=True)
    )


def add_selected_story(*, enriched: bool = True) -> Story:
    container = build_container()
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(container.settings.default_workspace_slug)
        assert workspace is not None
        story = Story(
            workspace_id=workspace.id,
            canonical_headline="An open model was released",
            primary_url="https://example.test/open-model",
            status=StoryStatus.SELECTED,
            first_seen_at=container.clock.now(),
            last_seen_at=container.clock.now(),
            selected_at=container.clock.now(),
        )
        if enriched:
            story.enrichment = EnrichedSource(
                url=story.primary_url or "",
                text=ARTICLE,
                fetched_at=container.clock.now(),
            )
        uow.stories.add(story)
        uow.commit()
    return story


class TestCheck:
    def test_it_reports_a_conforming_response(
        self, seeded: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from engine.cli.main import app

        install(monkeypatch, answering())

        result = runner.invoke(app, ["llm", "check"])

        assert result.exit_code == 0, result.stdout
        assert "Schema satisfied" in result.stdout
        assert "An open model" in result.stdout

    def test_it_names_the_provider_and_model_under_test(
        self, seeded: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Otherwise a green check says nothing about what was checked."""
        from engine.cli.main import app

        install(monkeypatch, answering())

        result = runner.invoke(app, ["llm", "check"])

        assert "ollama" in result.stdout
        assert "qwen3:1.7b" in result.stdout

    def test_local_generation_reports_no_cost(
        self, seeded: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from engine.cli.main import app

        install(monkeypatch, answering())

        result = runner.invoke(app, ["llm", "check"])

        assert "$0.000000" in result.stdout

    def test_a_provider_failure_reports_its_code(
        self, seeded: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from engine.cli.main import app

        install(monkeypatch, FakeLLM(fails_with="connection refused"))

        result = runner.invoke(app, ["llm", "check"])

        assert result.exit_code == 1
        assert "Check failed" in result.stdout


class TestCorpusProbe:
    def test_it_sends_the_real_article_text(
        self, seeded: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A toy prompt proves nothing about behaviour at article length."""
        from engine.cli.main import app

        fake = install(monkeypatch, answering())
        add_selected_story()

        result = runner.invoke(app, ["llm", "check", "--corpus"])

        assert result.exit_code == 0, result.stdout
        assert ARTICLE in fake.requests[0].prompt

    def test_an_empty_corpus_says_what_to_run(
        self, seeded: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from engine.cli.main import app

        install(monkeypatch, answering())

        result = runner.invoke(app, ["llm", "check", "--corpus"])

        assert result.exit_code == 1
        assert "engine select run" in result.stdout

    def test_an_unenriched_story_says_what_to_run(
        self, seeded: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Probing a story nobody read first-hand would test nothing useful."""
        from engine.cli.main import app

        install(monkeypatch, answering())
        add_selected_story(enriched=False)

        result = runner.invoke(app, ["llm", "check", "--corpus"])

        assert result.exit_code == 1
        assert "engine enrich run" in result.stdout

    def test_an_unknown_story_id_is_reported(
        self, seeded: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from engine.cli.main import app

        install(monkeypatch, answering())

        result = runner.invoke(app, ["llm", "check", "--story", "sty_missing"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
