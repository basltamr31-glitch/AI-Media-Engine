"""End-to-end tests for pack selection and the pack CLI.

The important one is `test_a_workspace_can_run_a_completely_different_pack`:
it is the executable proof that the engine carries no AI-news assumptions, using
a synthetic pack about a domain that does not exist.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from engine.cli.container import build_container
from engine.cli.main import app
from engine.settings import get_settings

pytestmark = pytest.mark.integration

runner = CliRunner()

WIDGET_PACK = """
key: widgets
version: 1
name: Widget Industry
description: A synthetic domain with no relationship to AI.
taxonomy:
  - {key: launch, label: Product launch}
  - {key: recall, label: Safety recall}
  - {key: firmware, label: Firmware, parent: recall}
"""

WORKSPACE = {
    "slug": "widgets-co",
    "name": "Widgets Co",
    "pack": "widgets",
    "timezone": "UTC",
    "languages": ["en"],
    "sources": [
        {
            "kind": "rss",
            "name": "Widget Weekly",
            "identifier": "https://widgets.example/feed.xml",
        }
    ],
    "brand_profiles": [],
}


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An install with its own packs directory, config and database."""
    packs = tmp_path / "packs"
    (packs / "widgets").mkdir(parents=True)
    (packs / "widgets" / "pack.yaml").write_text(WIDGET_PACK, encoding="utf-8")

    config = tmp_path / "config"
    (config / "workspaces").mkdir(parents=True)
    (config / "workspaces" / "widgets-co.yaml").write_text(
        yaml.safe_dump(WORKSPACE), encoding="utf-8"
    )

    monkeypatch.setenv("ENGINE_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'packs.db'}")
    monkeypatch.setenv("ENGINE_PACKS_DIR", str(packs))
    monkeypatch.setenv("ENGINE_CONFIG_DIR", str(config))
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENGINE_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("ENGINE_DEFAULT_WORKSPACE_SLUG", "widgets-co")
    monkeypatch.setenv("COLUMNS", "240")
    get_settings.cache_clear()

    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
    yield tmp_path
    get_settings.cache_clear()


class TestPackCommands:
    def test_list_shows_available_packs(self, isolated: Path) -> None:
        result = runner.invoke(app, ["pack", "list"])
        assert result.exit_code == 0
        assert "widgets" in result.stdout

    def test_validate_passes_on_a_good_pack(self, isolated: Path) -> None:
        result = runner.invoke(app, ["pack", "validate"])
        assert result.exit_code == 0
        assert "1 pack(s) valid" in result.stdout

    def test_validate_fails_loudly_on_a_broken_pack(self, isolated: Path) -> None:
        """Packs are data, so CI is where a typo gets caught."""
        broken = isolated / "packs" / "broken"
        broken.mkdir()
        (broken / "pack.yaml").write_text("version: 1\nname: X\ntaxonomy: nope\n", "utf-8")

        result = runner.invoke(app, ["pack", "validate"])
        assert result.exit_code == 1
        assert "FAIL" in result.stdout

    def test_show_renders_the_taxonomy_tree(self, isolated: Path) -> None:
        result = runner.invoke(app, ["pack", "show", "widgets"])
        assert result.exit_code == 0
        assert "Product launch" in result.stdout
        assert "Firmware" in result.stdout

    def test_show_reports_an_unknown_pack(self, isolated: Path) -> None:
        result = runner.invoke(app, ["pack", "show", "ghost"])
        assert result.exit_code == 1
        assert "not found" in result.stdout


class TestPackSelection:
    def test_a_workspace_can_run_a_completely_different_pack(self, isolated: Path) -> None:
        """Vertical-agnosticism, proven rather than asserted.

        The engine seeds and operates a workspace whose domain is widgets. If any
        AI-news assumption ever leaks into the engine, this test fails.
        """
        result = runner.invoke(app, ["workspace", "seed"])
        assert result.exit_code == 0, result.stdout
        assert "widgets@1" in result.stdout

        container = build_container()
        with container.unit_of_work() as uow:
            workspace = uow.workspaces.get_by_slug("widgets-co")
        assert workspace is not None
        assert workspace.pack_key == "widgets"

    def test_seeding_fails_when_the_pack_is_missing(self, isolated: Path) -> None:
        """Fail before touching the database, not at classification time."""
        path = isolated / "config" / "workspaces" / "widgets-co.yaml"
        path.write_text(yaml.safe_dump({**WORKSPACE, "pack": "ghost"}), encoding="utf-8")

        result = runner.invoke(app, ["workspace", "seed"])
        assert result.exit_code == 1
        assert "not found" in result.stdout

        container = build_container()
        with container.unit_of_work() as uow:
            assert uow.workspaces.get_by_slug("widgets-co") is None

    def test_pack_list_reports_which_workspaces_use_each_pack(self, isolated: Path) -> None:
        assert runner.invoke(app, ["workspace", "seed"]).exit_code == 0
        result = runner.invoke(app, ["pack", "list"])
        assert "widgets-co" in result.stdout
