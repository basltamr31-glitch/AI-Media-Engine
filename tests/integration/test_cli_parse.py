"""End-to-end CLI tests for the parsing commands.

Ingestion is stubbed but parsing is real: the documents these tests parse are
the captured TLDR issue in ``tests/fixtures``, run through the actual
registered parser. That makes this the test that would catch a parser wired up
but never reached -- registration, container construction and stage selection
all have to work for it to pass.
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
    monkeypatch.setenv("ENGINE_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'parse.db'}")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENGINE_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("ENGINE_GOOGLE_CREDENTIALS_FILE", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("ENGINE_GOOGLE_TOKEN_FILE", str(tmp_path / "token.json"))
    monkeypatch.setenv("COLUMNS", "240")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def seeded_with_issues(cli_env: Path) -> Path:
    """A workspace holding two real newsletter issues, ready to parse."""
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
    return cli_env


def count_candidates() -> int:
    container = build_container()
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(container.settings.default_workspace_slug)
        assert workspace is not None
        return uow.candidates.count_for_workspace(workspace.id)


class TestParseRun:
    def test_it_extracts_candidates_from_real_issues(self, seeded_with_issues: Path) -> None:
        result = runner.invoke(app, ["parse", "run"])
        assert result.exit_code == 0, result.stdout
        assert "Run succeeded" in result.stdout
        assert count_candidates() == 30  # 15 stories x 2 issues

    def test_the_results_table_names_the_parser(self, seeded_with_issues: Path) -> None:
        result = runner.invoke(app, ["parse", "run"])
        assert "tldr_newsletter" in result.stdout
        assert "TLDR AI" in result.stdout

    def test_a_second_run_extracts_nothing_new(self, seeded_with_issues: Path) -> None:
        assert runner.invoke(app, ["parse", "run"]).exit_code == 0
        result = runner.invoke(app, ["parse", "run"])

        assert result.exit_code == 0
        assert count_candidates() == 30

    def test_reparse_replaces_rather_than_duplicates(self, seeded_with_issues: Path) -> None:
        assert runner.invoke(app, ["parse", "run"]).exit_code == 0
        result = runner.invoke(app, ["parse", "run", "--reparse"])

        assert result.exit_code == 0, result.stdout
        assert count_candidates() == 30

    def test_a_dry_run_stores_nothing(self, seeded_with_issues: Path) -> None:
        result = runner.invoke(app, ["parse", "run", "--dry-run"])

        assert result.exit_code == 0
        assert "Dry run" in result.stdout
        assert count_candidates() == 0

    def test_it_can_be_narrowed_to_one_source(self, seeded_with_issues: Path) -> None:
        result = runner.invoke(app, ["parse", "run", "--source", "TLDR AI"])
        assert result.exit_code == 0
        assert count_candidates() == 30

    def test_the_document_budget_is_respected(self, seeded_with_issues: Path) -> None:
        result = runner.invoke(app, ["parse", "run", "--limit", "1"])
        assert result.exit_code == 0
        assert count_candidates() == 15

    def test_sources_without_a_parser_are_reported_not_fatal(
        self, seeded_with_issues: Path
    ) -> None:
        """arXiv and Hacker News have no parser until 2.2."""
        result = runner.invoke(app, ["parse", "run"])
        assert result.exit_code == 0
        assert "Run succeeded" in result.stdout

    def test_an_unknown_workspace_is_reported(self, cli_env: Path) -> None:
        assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
        result = runner.invoke(app, ["parse", "run", "--slug", "ghost"])
        assert result.exit_code == 1
        assert "not found" in result.stdout


class TestParseStatus:
    def test_it_reports_nothing_extracted_yet(self, seeded_with_issues: Path) -> None:
        result = runner.invoke(app, ["parse", "status"])
        assert result.exit_code == 0
        assert "No candidates yet" in result.stdout

    def test_it_counts_candidates_per_source(self, seeded_with_issues: Path) -> None:
        assert runner.invoke(app, ["parse", "run"]).exit_code == 0
        result = runner.invoke(app, ["parse", "status"])

        assert result.exit_code == 0
        assert "Total candidates: 30" in result.stdout
        assert "tldr_newsletter" in result.stdout

    def test_it_shows_recent_headlines_with_their_links(self, seeded_with_issues: Path) -> None:
        assert runner.invoke(app, ["parse", "run"]).exit_code == 0
        result = runner.invoke(app, ["parse", "status", "-n", "3"])

        assert "Most recent candidates" in result.stdout
        assert "https://" in result.stdout


class TestParserRegistration:
    def test_the_tldr_parser_is_discovered_from_entry_points(self, cli_env: Path) -> None:
        """It is a plugin like any other (ADR 0017)."""
        assert "tldr_newsletter" in build_container().parsers

    def test_it_is_listed_by_the_plugins_command(self, cli_env: Path) -> None:
        result = runner.invoke(app, ["plugins", "list", "--group", "parsers"])
        assert result.exit_code == 0
        assert "tldr_newsletter" in result.stdout
