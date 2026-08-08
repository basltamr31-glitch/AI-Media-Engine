"""End-to-end CLI tests for enrichment.

The network is stubbed at the container's HTTP client, so everything else --
settings, extractor construction, story selection, stage wiring, table
rendering -- is the real thing.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from engine.cli import container as container_module
from engine.cli.container import build_container
from engine.cli.main import app
from engine.core.domain.enums import StoryStatus
from engine.core.domain.story import Story
from engine.core.ports.http import HttpError
from engine.settings import get_settings
from tests.fakes import FakeHttpClient

pytestmark = pytest.mark.integration

runner = CliRunner()

PARAGRAPH = (
    "The company confirmed the change in a post published on its engineering blog, "
    "adding that the rollout would reach all regions within a fortnight."
)
ARTICLE = (
    "<html><head><title>A real announcement</title></head><body><article>"
    + "".join(f"<p>{PARAGRAPH} Point {i} covered the migration path.</p>" for i in range(6))
    + "</article></body></html>"
)


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("ENGINE_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'enrich.db'}")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENGINE_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("ENGINE_GOOGLE_CREDENTIALS_FILE", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("ENGINE_GOOGLE_TOKEN_FILE", str(tmp_path / "token.json"))
    monkeypatch.setenv("COLUMNS", "240")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def stub_web(monkeypatch: pytest.MonkeyPatch):
    """Replace the container's HTTP client, leaving the rest of the wiring real."""

    def install(responses: dict[str, str], *, error: Exception | None = None) -> None:
        client = FakeHttpClient(responses, error=error, content_type="text/html")
        monkeypatch.setattr(
            container_module.Container, "http", property(lambda self: client), raising=False
        )

    return install


@pytest.fixture
def resolved(cli_env: Path) -> Story:
    """A workspace holding one resolved story with a link and no enrichment."""
    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
    assert runner.invoke(app, ["workspace", "seed"]).exit_code == 0

    container = build_container()
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(container.settings.default_workspace_slug)
        assert workspace is not None
        story = Story(
            workspace_id=workspace.id,
            canonical_headline="A story worth reading",
            primary_url="https://example.com/post",
            status=StoryStatus.RESOLVED,
            first_seen_at=container.clock.now(),
            last_seen_at=container.clock.now(),
        )
        uow.stories.add(story)
        uow.commit()
    return story


def reload_story(story: Story) -> Story:
    container = build_container()
    with container.unit_of_work() as uow:
        found = uow.stories.get(story.workspace_id, story.id)
    assert found is not None
    return found


class TestEnrichRun:
    def test_it_fetches_and_extracts_the_primary_source(self, resolved: Story, stub_web) -> None:
        stub_web({"https://example.com/post": ARTICLE})

        result = runner.invoke(app, ["enrich", "run"])

        assert result.exit_code == 0, result.stdout
        enriched = reload_story(resolved)
        assert enriched.status is StoryStatus.ENRICHED
        assert "engineering blog" in (enriched.enrichment.text if enriched.enrichment else "")

    def test_the_results_table_is_rendered(self, resolved: Story, stub_web) -> None:
        stub_web({"https://example.com/post": ARTICLE})
        result = runner.invoke(app, ["enrich", "run"])
        assert "Enrichment results" in result.stdout

    def test_a_second_run_has_nothing_to_do(self, resolved: Story, stub_web) -> None:
        stub_web({"https://example.com/post": ARTICLE})
        assert runner.invoke(app, ["enrich", "run"]).exit_code == 0

        result = runner.invoke(app, ["enrich", "run"])
        assert "Nothing to enrich" in result.stdout

    def test_a_dry_run_stores_nothing(self, resolved: Story, stub_web) -> None:
        stub_web({"https://example.com/post": ARTICLE})
        result = runner.invoke(app, ["enrich", "run", "--dry-run"])

        assert "Dry run" in result.stdout
        assert reload_story(resolved).enrichment is None

    def test_failures_are_reported_without_failing_the_run(self, resolved: Story, stub_web) -> None:
        stub_web({}, error=HttpError("https://example.com/post", "403 Forbidden"))

        result = runner.invoke(app, ["enrich", "run"])

        assert result.exit_code == 0
        assert "Failures:" in result.stdout
        assert reload_story(resolved).enrichment is None

    def test_a_paywalled_page_is_rejected_by_the_real_extractor(
        self, resolved: Story, stub_web
    ) -> None:
        """The stub is only the network; extraction quality is the real thing."""
        stub_web({"https://example.com/post": "<html><body><p>Subscribers only.</p></body></html>"})

        result = runner.invoke(app, ["enrich", "run"])

        assert result.exit_code == 0
        assert reload_story(resolved).enrichment is None

    def test_one_story_can_be_targeted(self, resolved: Story, stub_web) -> None:
        stub_web({"https://example.com/post": ARTICLE})
        result = runner.invoke(app, ["enrich", "run", "--story", resolved.id])

        assert result.exit_code == 0, result.stdout
        assert reload_story(resolved).enrichment is not None

    def test_an_unknown_workspace_is_reported(self, cli_env: Path) -> None:
        assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
        result = runner.invoke(app, ["enrich", "run", "--slug", "ghost"])
        assert result.exit_code == 1
        assert "not found" in result.stdout


class TestEnrichStatus:
    def test_it_reports_coverage(self, resolved: Story, stub_web) -> None:
        stub_web({"https://example.com/post": ARTICLE})
        assert runner.invoke(app, ["enrich", "run"]).exit_code == 0

        result = runner.invoke(app, ["enrich", "status"])

        assert result.exit_code == 0
        assert "1 carry a primary source" in result.stdout

    def test_it_lists_what_could_not_be_read_and_why(self, resolved: Story, stub_web) -> None:
        stub_web({}, error=HttpError("https://example.com/post", "403 Forbidden"))
        assert runner.invoke(app, ["enrich", "run"]).exit_code == 0

        result = runner.invoke(app, ["enrich", "status"])

        assert "could not be read" in result.stdout
        assert "403" in result.stdout

    def test_a_clean_workspace_says_so(self, resolved: Story) -> None:
        result = runner.invoke(app, ["enrich", "status"])
        assert result.exit_code == 0
        assert "No enrichment failures" in result.stdout
