"""End-to-end CLI tests for the provenance ledger.

The chain under test is the whole of Phase 2: a captured newsletter is
ingested, parsed, resolved into stories, and then asked what supports it. If
any link in that chain breaks, this file goes red.
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
from engine.core.domain.story import Story
from engine.settings import get_settings

pytestmark = pytest.mark.integration

runner = CliRunner()

FIXTURE = Path(__file__).parent.parent / "fixtures" / "tldr_ai_issue.html"


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("ENGINE_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'evidence.db'}")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENGINE_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("ENGINE_GOOGLE_CREDENTIALS_FILE", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("ENGINE_GOOGLE_TOKEN_FILE", str(tmp_path / "token.json"))
    monkeypatch.setenv("COLUMNS", "240")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def resolved(cli_env: Path) -> Path:
    """A real newsletter, taken through the whole Phase 2 chain."""
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
        uow.documents.add(
            SourceDocument(
                workspace_id=workspace.id,
                source_id=source.id,
                content_hash=compute_content_hash(markup),
                title="TLDR AI issue",
                content_type="text/html",
                raw_content=markup,
                fetched_at=container.clock.now(),
            )
        )
        uow.commit()

    assert runner.invoke(app, ["parse", "run"]).exit_code == 0
    assert runner.invoke(app, ["resolve", "run"]).exit_code == 0
    return cli_env


def first_story() -> Story:
    container = build_container()
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(container.settings.default_workspace_slug)
        assert workspace is not None
        return uow.stories.list_recent(workspace.id, limit=1)[0]


def ledger_size() -> int:
    container = build_container()
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(container.settings.default_workspace_slug)
        assert workspace is not None
        return uow.evidence.count_for_workspace(workspace.id)


class TestEvidenceRun:
    def test_every_story_gets_its_provenance(self, resolved: Path) -> None:
        result = runner.invoke(app, ["evidence", "run"])

        assert result.exit_code == 0, result.stdout
        assert ledger_size() == 15  # one aggregator record per story in the issue

    def test_a_newsletter_only_workspace_is_flagged(self, resolved: Path) -> None:
        """Nothing here has been read first-hand, and the output says so."""
        result = runner.invoke(app, ["evidence", "run"])

        assert "Aggregator-only stories" in result.stdout
        assert "cannot be written about" in result.stdout

    def test_running_twice_records_nothing_new(self, resolved: Path) -> None:
        assert runner.invoke(app, ["evidence", "run"]).exit_code == 0
        result = runner.invoke(app, ["evidence", "run"])

        assert result.exit_code == 0
        assert ledger_size() == 15

    def test_a_dry_run_records_nothing(self, resolved: Path) -> None:
        result = runner.invoke(app, ["evidence", "run", "--dry-run"])

        assert result.exit_code == 0
        assert ledger_size() == 0

    def test_an_empty_workspace_says_so(self, cli_env: Path) -> None:
        assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
        assert runner.invoke(app, ["workspace", "seed"]).exit_code == 0

        result = runner.invoke(app, ["evidence", "run"])
        assert result.exit_code == 0
        assert "Nothing to reconcile" in result.stdout

    def test_an_unknown_workspace_is_reported(self, cli_env: Path) -> None:
        assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
        result = runner.invoke(app, ["evidence", "run", "--slug", "ghost"])
        assert result.exit_code == 1
        assert "not found" in result.stdout


class TestEvidenceShow:
    def test_it_lists_what_supports_a_story(self, resolved: Path) -> None:
        assert runner.invoke(app, ["evidence", "run"]).exit_code == 0
        story = first_story()

        result = runner.invoke(app, ["evidence", "show", story.id])

        assert result.exit_code == 0, result.stdout
        assert "aggregator" in result.stdout
        assert "TLDR AI" in result.stdout

    def test_it_warns_when_nothing_was_read_first_hand(self, resolved: Path) -> None:
        assert runner.invoke(app, ["evidence", "run"]).exit_code == 0

        result = runner.invoke(app, ["evidence", "show", first_story().id])
        assert "Only aggregator evidence" in result.stdout

    def test_the_short_id_works(self, resolved: Path) -> None:
        assert runner.invoke(app, ["evidence", "run"]).exit_code == 0
        result = runner.invoke(app, ["evidence", "show", first_story().id[-8:]])
        assert result.exit_code == 0, result.stdout

    def test_a_story_with_no_evidence_says_so(self, resolved: Path) -> None:
        result = runner.invoke(app, ["evidence", "show", first_story().id])
        assert result.exit_code == 0
        assert "No evidence recorded" in result.stdout

    def test_an_unknown_story_is_reported(self, resolved: Path) -> None:
        result = runner.invoke(app, ["evidence", "show", "sty_nope"])
        assert result.exit_code == 1
        assert "not found" in result.stdout


class TestStoriesShowIntegration:
    def test_stories_show_summarises_the_evidence(self, resolved: Path) -> None:
        assert runner.invoke(app, ["evidence", "run"]).exit_code == 0

        result = runner.invoke(app, ["stories", "show", first_story().id])

        assert result.exit_code == 0, result.stdout
        assert "Evidence: aggregator=1" in result.stdout

    def test_it_prompts_when_evidence_is_missing(self, resolved: Path) -> None:
        result = runner.invoke(app, ["stories", "show", first_story().id])
        assert "No evidence recorded" in result.stdout
