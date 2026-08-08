"""End-to-end CLI tests for generation.

These run against the shipped `default` workspace configuration -- its real
brand profiles and the real ai-news pack -- so a broken config file fails here
rather than in front of a customer. The model is faked; whether one can write
well is settled by running a real one, not by asserting against a double.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from engine.adapters.generation.prompts import GeneratedContent
from engine.cli.container import Container, build_container
from engine.cli.main import app
from engine.core.domain.enums import StoryStatus
from engine.core.domain.story import EnrichedSource, Story
from engine.settings import get_settings
from tests.fakes import FakeLLM

pytestmark = pytest.mark.integration

runner = CliRunner()

ARTICLE = "The laboratory released an open-weights model and published the training recipe."


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("ENGINE_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'gen.db'}")
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
    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
    assert runner.invoke(app, ["workspace", "seed"]).exit_code == 0
    return cli_env


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeLLM) -> FakeLLM:
    monkeypatch.setattr(Container, "llm", property(lambda self: fake))
    return fake


def answering(body: str = "A short, factual post about the release.") -> FakeLLM:
    return FakeLLM(
        value=GeneratedContent(
            body=body,
            hashtags=["ai"],
            source_url="https://example.test/model",
        )
    )


def add_selected_story() -> Story:
    container = build_container()
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(container.settings.default_workspace_slug)
        assert workspace is not None
        story = Story(
            workspace_id=workspace.id,
            canonical_headline="An open model was released",
            primary_url="https://example.test/model",
            status=StoryStatus.SELECTED,
            first_seen_at=container.clock.now(),
            last_seen_at=container.clock.now(),
            selected_at=container.clock.now(),
        )
        story.enrichment = EnrichedSource(
            url=story.primary_url or "", text=ARTICLE, fetched_at=container.clock.now()
        )
        uow.stories.add(story)
        uow.commit()
    return story


class TestGenerateRun:
    def test_it_writes_for_the_shipped_workspace_configuration(
        self, seeded: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exercises the real brand profiles and the real ai-news pack."""
        install(monkeypatch, answering())
        add_selected_story()

        result = runner.invoke(app, ["generate", "run"])

        assert result.exit_code == 0, result.stdout
        assert "Wrote" in result.stdout

    def test_it_names_the_voices_it_is_writing_in(
        self, seeded: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, answering())
        add_selected_story()

        result = runner.invoke(app, ["generate", "run"])

        assert "default_ar" in result.stdout
        assert "default_en" in result.stdout

    def test_a_channel_can_be_selected(self, seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        install(monkeypatch, answering())
        add_selected_story()

        result = runner.invoke(app, ["generate", "run", "--channel", "x"])

        assert result.exit_code == 0, result.stdout
        listed = runner.invoke(app, ["generate", "list"])
        assert "linkedin" not in listed.stdout

    def test_an_empty_selection_says_so(
        self, seeded: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, answering())

        result = runner.invoke(app, ["generate", "run"])

        assert result.exit_code == 0
        assert "Nothing to generate" in result.stdout

    def test_a_dry_run_stores_nothing(self, seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        install(monkeypatch, answering())
        add_selected_story()

        result = runner.invoke(app, ["generate", "run", "--dry-run"])

        assert "Dry run" in result.stdout
        assert "Nothing generated yet" in runner.invoke(app, ["generate", "list"]).stdout

    def test_refusals_are_reported_in_plain_english(
        self, seeded: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, FakeLLM(fails_with="connection refused"))
        add_selected_story()

        result = runner.invoke(app, ["generate", "run"])

        assert result.exit_code == 0
        assert "the model call failed" in result.stdout

    def test_an_unknown_workspace_is_reported(self, cli_env: Path) -> None:
        assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
        result = runner.invoke(app, ["generate", "run", "--slug", "ghost"])
        assert result.exit_code == 1
        assert "not found" in result.stdout


class TestGenerateListAndShow:
    def test_an_empty_workspace_says_what_to_run(self, seeded: Path) -> None:
        result = runner.invoke(app, ["generate", "list"])
        assert result.exit_code == 0
        assert "engine generate run" in result.stdout

    def test_show_prints_the_body_and_its_provenance(
        self, seeded: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, answering("The lab shipped open weights today."))
        add_selected_story()
        assert runner.invoke(app, ["generate", "run", "--channel", "x"]).exit_code == 0

        container = build_container()
        with container.unit_of_work() as uow:
            workspace = uow.workspaces.get_by_slug("default")
            assert workspace is not None
            asset = uow.assets.list_recent(workspace.id, limit=1)[0]

        result = runner.invoke(app, ["generate", "show", asset.id])

        assert result.exit_code == 0, result.stdout
        assert "The lab shipped open weights today." in result.stdout
        assert "https://example.test/model" in result.stdout

    def test_history_lists_every_revision(
        self, seeded: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_selected_story()
        for body in ("First attempt.", "Second attempt."):
            install(monkeypatch, answering(body))
            assert runner.invoke(app, ["generate", "run", "--channel", "x"]).exit_code == 0

        container = build_container()
        with container.unit_of_work() as uow:
            workspace = uow.workspaces.get_by_slug("default")
            assert workspace is not None
            asset = uow.assets.list_recent(workspace.id, limit=1)[0]

        result = runner.invoke(app, ["generate", "show", asset.id, "--history"])

        assert result.exit_code == 0, result.stdout
        assert "generated" in result.stdout
        assert "Second attempt." in result.stdout

    def test_an_unknown_asset_is_reported(self, seeded: Path) -> None:
        result = runner.invoke(app, ["generate", "show", "ast_missing"])
        assert result.exit_code == 1
        assert "not found" in result.stdout


class TestCostReport:
    def test_it_says_so_when_nothing_has_been_generated(self, seeded: Path) -> None:
        result = runner.invoke(app, ["cost", "report"])

        assert result.exit_code == 0
        assert "No generation spend recorded" in result.stdout

    def test_it_itemises_spend_per_asset(
        self, seeded: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, answering())
        add_selected_story()
        assert runner.invoke(app, ["generate", "run", "--channel", "x"]).exit_code == 0

        result = runner.invoke(app, ["cost", "report"])

        assert result.exit_code == 0, result.stdout
        assert "Most expensive assets" in result.stdout
        assert "generation(s)" in result.stdout

    def test_zero_cost_is_explained_rather_than_left_looking_broken(
        self, seeded: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A table of zeroes otherwise reads as missing instrumentation."""
        install(monkeypatch, answering())
        add_selected_story()
        assert runner.invoke(app, ["generate", "run", "--channel", "x"]).exit_code == 0

        result = runner.invoke(app, ["cost", "report"])

        assert "local" in result.stdout

    def test_an_unknown_workspace_is_reported(self, cli_env: Path) -> None:
        assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
        result = runner.invoke(app, ["cost", "report", "--slug", "ghost"])
        assert result.exit_code == 1
        assert "not found" in result.stdout
