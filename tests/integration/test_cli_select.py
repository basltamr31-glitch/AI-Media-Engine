"""End-to-end CLI tests for selection.

The output under test is mostly the *refusals*: a run that selects nothing must
say which gate did it, because "a thin news day" and "the pipeline is broken"
look identical otherwise and call for opposite responses.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from engine.cli.container import build_container
from engine.cli.main import app
from engine.core.domain.enums import StoryStatus
from engine.core.domain.story import EditorialScore, EnrichedSource, Story
from engine.settings import get_settings

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("ENGINE_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'select.db'}")
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


def add_story(*, headline: str, score: float, enriched: bool = True) -> Story:
    container = build_container()
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(container.settings.default_workspace_slug)
        assert workspace is not None
        story = Story(
            workspace_id=workspace.id,
            canonical_headline=headline,
            primary_url=f"https://example.test/{headline.replace(' ', '-').lower()}",
            status=StoryStatus.RESOLVED,
            first_seen_at=container.clock.now(),
            last_seen_at=container.clock.now(),
        )
        if enriched:
            story.enrichment = EnrichedSource(
                url=story.primary_url or "",
                text="The announcement in full.",
                fetched_at=container.clock.now(),
            )
            story.status = StoryStatus.ENRICHED
        uow.stories.add(story)
        uow.scores.add_many(
            [
                EditorialScore(
                    workspace_id=workspace.id,
                    story_id=story.id,
                    run_id="run_seed",
                    total=score,
                    components={"corroboration": score},
                    scored_at=container.clock.now(),
                )
            ]
        )
        uow.commit()
    return story


def selected_count() -> int:
    container = build_container()
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(container.settings.default_workspace_slug)
        assert workspace is not None
        return len(uow.stories.list_selected(workspace.id, limit=100))


class TestSelectRun:
    def test_it_selects_the_best_stories_up_to_the_quota(self, seeded: Path) -> None:
        """The shipped policy allows three."""
        for index in range(5):
            add_story(headline=f"Story {index}", score=0.9 - index / 100)

        result = runner.invoke(app, ["select", "run"])

        assert result.exit_code == 0, result.stdout
        assert selected_count() == 3
        assert "Story 0" in result.stdout

    def test_a_second_run_does_not_produce_a_second_batch(self, seeded: Path) -> None:
        for index in range(5):
            add_story(headline=f"Story {index}", score=0.9)
        assert runner.invoke(app, ["select", "run"]).exit_code == 0

        result = runner.invoke(app, ["select", "run"])

        assert result.exit_code == 0
        assert selected_count() == 3
        assert "already selected in that window" in result.stdout

    def test_it_names_the_gate_that_refused(self, seeded: Path) -> None:
        add_story(headline="Unread", score=0.9, enriched=False)

        result = runner.invoke(app, ["select", "run"])

        assert result.exit_code == 0
        assert "no primary source read first-hand" in result.stdout
        assert "engine enrich run" in result.stdout

    def test_a_low_scoring_story_is_refused_for_its_score(self, seeded: Path) -> None:
        add_story(headline="Weak", score=0.1)

        result = runner.invoke(app, ["select", "run"])

        assert "scored below the minimum" in result.stdout
        assert selected_count() == 0

    def test_the_quota_can_be_overridden(self, seeded: Path) -> None:
        for index in range(5):
            add_story(headline=f"Story {index}", score=0.9)

        result = runner.invoke(app, ["select", "run", "--limit", "5"])

        assert result.exit_code == 0, result.stdout
        assert selected_count() == 5

    def test_a_dry_run_selects_nothing(self, seeded: Path) -> None:
        add_story(headline="Story", score=0.9)

        result = runner.invoke(app, ["select", "run", "--dry-run"])

        assert "Dry run" in result.stdout
        assert selected_count() == 0

    def test_an_empty_workspace_says_so(self, seeded: Path) -> None:
        result = runner.invoke(app, ["select", "run"])
        assert result.exit_code == 0
        assert "Nothing to select" in result.stdout

    def test_an_unknown_workspace_is_reported(self, cli_env: Path) -> None:
        assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
        result = runner.invoke(app, ["select", "run", "--slug", "ghost"])
        assert result.exit_code == 1
        assert "not found" in result.stdout


class TestSelectList:
    def test_it_reports_an_empty_selection(self, seeded: Path) -> None:
        result = runner.invoke(app, ["select", "list"])
        assert result.exit_code == 0
        assert "Nothing selected yet" in result.stdout

    def test_it_lists_what_was_chosen_with_rank_and_score(self, seeded: Path) -> None:
        add_story(headline="Chosen story", score=0.87)
        assert runner.invoke(app, ["select", "run"]).exit_code == 0

        result = runner.invoke(app, ["select", "list"])

        assert result.exit_code == 0
        assert "Chosen story" in result.stdout
        assert "0.8700" in result.stdout
