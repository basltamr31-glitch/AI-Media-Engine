"""End-to-end CLI tests for rendering."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from engine.cli.container import build_container
from engine.cli.main import app
from engine.core.domain.content import ContentAsset, RevisionOrigin
from engine.core.domain.enums import StoryStatus
from engine.core.domain.story import EnrichedSource, Story
from engine.settings import get_settings

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("ENGINE_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'render.db'}")
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


def add_edition_material(body: str = "The lab shipped open weights.") -> None:
    container = build_container()
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug("default")
        assert workspace is not None
        story = Story(
            workspace_id=workspace.id,
            canonical_headline="Open weights released",
            primary_url="https://arxiv.org/abs/1",
            status=StoryStatus.SELECTED,
            first_seen_at=container.clock.now(),
            last_seen_at=container.clock.now(),
            selected_at=container.clock.now(),
        )
        story.enrichment = EnrichedSource(
            url="https://arxiv.org/abs/1", text="The abstract.", fetched_at=container.clock.now()
        )
        uow.stories.add(story)

        asset = ContentAsset(
            workspace_id=workspace.id,
            story_id=story.id,
            run_id="run_seed",
            product_key="social_post",
            channel_key="linkedin",
            language="en",
            brand_profile_key="default_en",
            meta={"hook": "A hook.", "hashtags": ["ai"]},
        )
        uow.assets.add(asset)
        revision = asset.next_revision(
            body, origin=RevisionOrigin.GENERATED, actor_id="actor_system", prompt_version="gen-4"
        )
        uow.assets.add_revision(revision)
        asset.current_revision_id = revision.id
        uow.assets.update(asset)
        uow.commit()


class TestRenderRun:
    def test_it_writes_an_edition(self, seeded: Path) -> None:
        add_edition_material()

        result = runner.invoke(app, ["render", "run", "--edition", "2026-08-08"])

        assert result.exit_code == 0, result.stdout
        assert "2026-08-08" in result.stdout
        assert (seeded / "output" / "default" / "2026-08-08" / "post.md").is_file()

    def test_a_thin_day_says_what_to_run(self, seeded: Path) -> None:
        result = runner.invoke(app, ["render", "run", "--edition", "2026-08-08"])

        assert result.exit_code == 0
        assert "thin day" in result.stdout

    def test_a_dry_run_writes_nothing(self, seeded: Path) -> None:
        add_edition_material()

        result = runner.invoke(app, ["render", "run", "--dry-run"])

        assert "Dry run" in result.stdout
        assert not (seeded / "output" / "default").exists()

    def test_an_unknown_workspace_is_reported(self, cli_env: Path) -> None:
        assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
        result = runner.invoke(app, ["render", "run", "--slug", "ghost"])
        assert result.exit_code == 1
        assert "not found" in result.stdout


class TestRenderShow:
    def test_it_summarises_the_latest_edition(self, seeded: Path) -> None:
        add_edition_material()
        assert runner.invoke(app, ["render", "run", "--edition", "2026-08-08"]).exit_code == 0

        result = runner.invoke(app, ["render", "show"])

        assert result.exit_code == 0, result.stdout
        assert "2026-08-08" in result.stdout
        assert "linkedin" in result.stdout

    def test_a_named_edition_can_be_shown(self, seeded: Path) -> None:
        add_edition_material()
        assert runner.invoke(app, ["render", "run", "--edition", "2026-08-08"]).exit_code == 0

        result = runner.invoke(app, ["render", "show", "2026-08-08"])

        assert result.exit_code == 0, result.stdout

    def test_nothing_rendered_yet_says_what_to_run(self, seeded: Path) -> None:
        result = runner.invoke(app, ["render", "show"])

        assert result.exit_code == 0
        assert "engine render run" in result.stdout

    def test_an_unknown_edition_is_reported(self, seeded: Path) -> None:
        add_edition_material()
        assert runner.invoke(app, ["render", "run", "--edition", "2026-08-08"]).exit_code == 0

        result = runner.invoke(app, ["render", "show", "1999-01-01"])

        assert result.exit_code == 1
        assert "No edition" in result.stdout
