"""End-to-end CLI tests for the ingestion commands.

These run the real Typer app against an isolated database, with the network
transports replaced. They cover the wiring that unit tests skip: container
construction, command registration, settings resolution and table rendering.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from engine.adapters.sources.registry import FetcherRegistry
from engine.cli import container as container_module
from engine.cli.container import build_container
from engine.cli.main import app
from engine.core.domain.enums import SourceKind
from engine.settings import get_settings
from tests.fakes import GeneratingFakeFetcher

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the CLI at an isolated database, archive and credential paths."""
    monkeypatch.setenv("ENGINE_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENGINE_OUTPUT_DIR", str(tmp_path / "output"))
    # Never let a test touch the developer's real Google credentials.
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


@pytest.fixture
def stub_fetchers(monkeypatch: pytest.MonkeyPatch):
    """Replace the real transports with fakes for every registered kind.

    Patching the container's ``fetchers`` property rather than the fetchers
    themselves keeps the rest of the wiring -- settings, database, stage
    construction, ledger -- completely real.
    """

    def build(documents_per_source: int = 2, *, generation: str = "a"):
        def fake_fetchers(self):
            registry = FetcherRegistry()
            for kind in (SourceKind.EMAIL, SourceKind.RSS, SourceKind.API):
                registry.register(
                    GeneratingFakeFetcher(
                        kind,
                        count=documents_per_source,
                        clock=self.clock,
                        generation=generation,
                    )
                )
            return registry

        monkeypatch.setattr(
            container_module.Container, "fetchers", property(fake_fetchers), raising=False
        )

    return build


def count_all_documents() -> int:
    """Count stored documents through the real container and repositories."""
    container = build_container()
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(container.settings.default_workspace_slug)
        assert workspace is not None
        sources = uow.sources.list_for_workspace(workspace.id, only_active=False)
        return sum(uow.documents.count_for_source(workspace.id, s.id) for s in sources)


class TestSourcesCommands:
    def test_status_reports_missing_credentials(self, cli_env: Path) -> None:
        result = runner.invoke(app, ["sources", "status"])
        assert result.exit_code == 0
        assert "missing" in result.stdout
        assert "not authorised" in result.stdout

    def test_status_lists_registered_fetchers(self, cli_env: Path) -> None:
        result = runner.invoke(app, ["sources", "status"])
        for kind in ("email", "rss", "api"):
            assert kind in result.stdout

    def test_auth_without_client_secrets_explains_how_to_get_them(self, cli_env: Path) -> None:
        result = runner.invoke(app, ["sources", "auth"])
        assert result.exit_code == 1
        assert "Desktop app" in result.stdout

    def test_logout_without_a_token_is_not_an_error(self, cli_env: Path) -> None:
        result = runner.invoke(app, ["sources", "logout"])
        assert result.exit_code == 0
        assert "No cached token" in result.stdout


class TestIngestCommands:
    def test_status_before_any_run_shows_zero_documents(self, seeded: Path) -> None:
        result = runner.invoke(app, ["ingest", "status"])
        assert result.exit_code == 0
        assert "TLDR AI" in result.stdout
        assert "arXiv cs.AI" in result.stdout

    def test_run_stores_documents_and_reports_them(self, seeded: Path, stub_fetchers) -> None:
        stub_fetchers(2)
        result = runner.invoke(app, ["ingest", "run"])
        assert result.exit_code == 0, result.stdout
        assert "succeeded" in result.stdout
        assert "Ingestion results" in result.stdout

    def test_second_run_stores_nothing_new(self, seeded: Path, stub_fetchers) -> None:
        """The idempotency guarantee, observed end to end through the CLI."""
        stub_fetchers(2)
        assert runner.invoke(app, ["ingest", "run"]).exit_code == 0
        after_first = count_all_documents()
        assert after_first == 6  # 3 seeded sources x 2 documents

        assert runner.invoke(app, ["ingest", "run"]).exit_code == 0
        assert count_all_documents() == after_first

    def test_a_moving_feed_adds_only_the_new_items(self, seeded: Path, stub_fetchers) -> None:
        stub_fetchers(2, generation="a")
        assert runner.invoke(app, ["ingest", "run"]).exit_code == 0
        assert count_all_documents() == 6

        stub_fetchers(2, generation="b")  # the feed has moved on
        assert runner.invoke(app, ["ingest", "run"]).exit_code == 0
        assert count_all_documents() == 12

    def test_dry_run_stores_nothing(self, seeded: Path, stub_fetchers) -> None:
        stub_fetchers(2)
        result = runner.invoke(app, ["ingest", "run", "--dry-run"])
        assert result.exit_code == 0
        assert "nothing was stored" in result.stdout
        assert count_all_documents() == 0

    def test_source_filter_targets_one_feed(self, seeded: Path, stub_fetchers) -> None:
        stub_fetchers(2)
        result = runner.invoke(app, ["ingest", "run", "--source", "arXiv cs.AI"])
        assert result.exit_code == 0
        assert "arXiv cs.AI" in result.stdout
        assert "Hacker News" not in result.stdout

    def test_unknown_workspace_is_rejected(self, seeded: Path) -> None:
        result = runner.invoke(app, ["ingest", "run", "--slug", "does-not-exist"])
        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_run_appears_in_the_pipeline_ledger(self, seeded: Path, stub_fetchers) -> None:
        stub_fetchers(1)
        assert runner.invoke(app, ["ingest", "run"]).exit_code == 0

        runs = runner.invoke(app, ["pipeline", "runs"])
        assert runs.exit_code == 0
        assert "ingest" in runs.stdout


class TestPipelineIntegration:
    def test_the_daily_pipeline_runs_every_stage_end_to_end(
        self, seeded: Path, stub_fetchers
    ) -> None:
        """Ingestion is wired into the real pipeline, not a side path.

        Asserts on the stage *names* rather than a count. The count changed
        silently when the pipeline grew from one stage to eight, and a test
        that only counts would have passed just as happily if a stage had been
        dropped instead of added.
        """
        stub_fetchers(1)

        result = runner.invoke(app, ["pipeline", "run"])

        assert result.exit_code == 0, result.stdout
        stages = runner.invoke(app, ["pipeline", "runs"])
        assert "daily_content" in stages.stdout

    def test_the_daily_pipeline_stops_before_rendering(self, seeded: Path, stub_fetchers) -> None:
        """Unattended work ends by filling the review queue (ADR 0018).

        Rendering an edition on a timer would publish a day's content as
        finished when nothing had been read by a human.
        """
        from engine.cli.commands.pipeline import build_daily_stages
        from engine.cli.container import build_container

        container = build_container()
        with container.unit_of_work() as uow:
            workspace = uow.workspaces.get_by_slug(container.settings.default_workspace_slug)
        assert workspace is not None

        names = [stage.name for stage in build_daily_stages(container, workspace)]

        assert names == [
            "ingest",
            "parse",
            "resolve",
            "enrich",
            "evidence",
            "score",
            "select",
            "generate",
        ]
