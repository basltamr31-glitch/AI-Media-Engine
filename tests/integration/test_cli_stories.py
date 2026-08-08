"""End-to-end CLI tests for resolution and story inspection.

These run the whole chain the operator runs -- seed, ingest a captured
newsletter, parse, resolve, inspect -- so a break anywhere in the wiring shows
up here rather than in production.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from engine.cli.container import build_container
from engine.cli.main import app
from engine.core.domain.enums import SourceKind
from engine.core.domain.source import SourceDocument, compute_content_hash
from engine.settings import get_settings

pytestmark = pytest.mark.integration

runner = CliRunner()

FIXTURE = Path(__file__).parent.parent / "fixtures" / "tldr_ai_issue.html"


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("ENGINE_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'stories.db'}")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENGINE_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("ENGINE_GOOGLE_CREDENTIALS_FILE", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("ENGINE_GOOGLE_TOKEN_FILE", str(tmp_path / "token.json"))
    monkeypatch.setenv("COLUMNS", "240")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def parsed(cli_env: Path) -> Path:
    """A workspace whose captured newsletter has been ingested and parsed.

    The same issue is stored twice under different hashes, which is exactly the
    situation resolution exists for: every story in it appears twice.
    """
    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
    assert runner.invoke(app, ["workspace", "seed"]).exit_code == 0

    markup = FIXTURE.read_text(encoding="utf-8")
    container = build_container()
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(container.settings.default_workspace_slug)
        assert workspace is not None
        source = next(
            s for s in uow.sources.list_for_workspace(workspace.id) if s.kind is SourceKind.EMAIL
        )
        for index in range(2):
            uow.documents.add(
                SourceDocument(
                    workspace_id=workspace.id,
                    source_id=source.id,
                    content_hash=compute_content_hash(markup, str(index)),
                    title=f"TLDR AI issue {index}",
                    content_type="text/html",
                    raw_content=markup,
                    fetched_at=container.clock.now(),
                )
            )
        uow.commit()

    assert runner.invoke(app, ["parse", "run"]).exit_code == 0
    return cli_env


def story_count() -> int:
    container = build_container()
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(container.settings.default_workspace_slug)
        assert workspace is not None
        return uow.stories.count_for_workspace(workspace.id)


class TestResolveRun:
    def test_duplicate_mentions_collapse_into_one_story_each(self, parsed: Path) -> None:
        """Two copies of one issue: 30 mentions, 15 stories."""
        result = runner.invoke(app, ["resolve", "run"])
        assert result.exit_code == 0, result.stdout
        assert story_count() == 15

    def test_the_output_says_how_matches_were_made(self, parsed: Path) -> None:
        result = runner.invoke(app, ["resolve", "run"])
        assert "Matched by:" in result.stdout
        assert "url=15" in result.stdout

    def test_a_second_run_creates_nothing(self, parsed: Path) -> None:
        assert runner.invoke(app, ["resolve", "run"]).exit_code == 0
        result = runner.invoke(app, ["resolve", "run"])

        assert result.exit_code == 0
        assert story_count() == 15

    def test_a_dry_run_stores_nothing(self, parsed: Path) -> None:
        result = runner.invoke(app, ["resolve", "run", "--dry-run"])

        assert result.exit_code == 0
        assert "Dry run" in result.stdout
        assert story_count() == 0

    def test_the_threshold_can_be_overridden(self, parsed: Path) -> None:
        result = runner.invoke(app, ["resolve", "run", "--similarity", "0.5"])
        assert result.exit_code == 0
        assert "0.5" in result.stdout

    def test_an_unknown_workspace_is_reported(self, cli_env: Path) -> None:
        assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
        result = runner.invoke(app, ["resolve", "run", "--slug", "ghost"])
        assert result.exit_code == 1
        assert "not found" in result.stdout


class TestStoriesList:
    def test_it_reports_an_empty_workspace(self, parsed: Path) -> None:
        result = runner.invoke(app, ["stories", "list"])
        assert result.exit_code == 0
        assert "No stories yet" in result.stdout

    def test_it_lists_resolved_stories(self, parsed: Path) -> None:
        assert runner.invoke(app, ["resolve", "run"]).exit_code == 0
        result = runner.invoke(app, ["stories", "list", "-n", "5"])

        assert result.exit_code == 0
        assert "Total: 15" in result.stdout
        assert "resolved=15" in result.stdout

    def test_corroboration_is_queried_not_filtered_from_a_page(self, parsed: Path) -> None:
        """One source twice is not corroboration, however many mentions there are."""
        assert runner.invoke(app, ["resolve", "run"]).exit_code == 0
        result = runner.invoke(app, ["stories", "list", "--corroborated"])

        assert result.exit_code == 0
        assert "more than one source" in result.stdout


class TestStoriesShow:
    def test_it_shows_every_mention_behind_a_story(self, parsed: Path) -> None:
        assert runner.invoke(app, ["resolve", "run"]).exit_code == 0
        container = build_container()
        with container.unit_of_work() as uow:
            workspace = uow.workspaces.get_by_slug(container.settings.default_workspace_slug)
            assert workspace is not None
            story = uow.stories.list_recent(workspace.id, limit=1)[0]

        result = runner.invoke(app, ["stories", "show", story.id])

        assert result.exit_code == 0, result.stdout
        assert "Mentions" in result.stdout
        assert "TLDR AI" in result.stdout
        assert "2 mention(s)" in result.stdout

    def test_the_short_id_from_the_listing_works(self, parsed: Path) -> None:
        assert runner.invoke(app, ["resolve", "run"]).exit_code == 0
        container = build_container()
        with container.unit_of_work() as uow:
            workspace = uow.workspaces.get_by_slug(container.settings.default_workspace_slug)
            assert workspace is not None
            story = uow.stories.list_recent(workspace.id, limit=1)[0]

        result = runner.invoke(app, ["stories", "show", story.id[-8:]])
        assert result.exit_code == 0, result.stdout

    def test_an_unknown_story_is_reported(self, parsed: Path) -> None:
        result = runner.invoke(app, ["stories", "show", "sty_nope"])
        assert result.exit_code == 1
        assert "not found" in result.stdout


class TestStoriesReset:
    def test_it_returns_mentions_to_the_unresolved_pool(self, parsed: Path) -> None:
        """Without this, a changed threshold has nothing left to act on."""
        assert runner.invoke(app, ["resolve", "run"]).exit_code == 0
        result = runner.invoke(app, ["stories", "reset", "--yes"])

        assert result.exit_code == 0
        assert story_count() == 0

        assert runner.invoke(app, ["resolve", "run"]).exit_code == 0
        assert story_count() == 15

    def test_resetting_an_empty_workspace_is_not_an_error(self, parsed: Path) -> None:
        result = runner.invoke(app, ["stories", "reset", "--yes"])
        assert result.exit_code == 0
        assert "No stories" in result.stdout

    def test_it_asks_before_discarding(self, parsed: Path) -> None:
        assert runner.invoke(app, ["resolve", "run"]).exit_code == 0
        result = runner.invoke(app, ["stories", "reset"], input="n\n")

        assert result.exit_code != 0
        assert story_count() == 15
