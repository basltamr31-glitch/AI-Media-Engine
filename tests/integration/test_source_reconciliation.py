"""Tests for source reconciliation during `engine workspace seed`.

These cover a defect found by running Phase 1 against live feeds rather than by
any unit test: changing a source's URL in config used to fork a second source
and orphan the original's ingestion history.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from engine.cli.container import build_container
from engine.cli.main import app
from engine.settings import get_settings

pytestmark = pytest.mark.integration

runner = CliRunner()

BASE_CONFIG: dict[str, Any] = {
    "slug": "recon",
    "name": "Reconciliation Workspace",
    "timezone": "UTC",
    "languages": ["en"],
    "sources": [
        {
            "kind": "rss",
            "name": "arXiv cs.AI",
            "identifier": "http://export.arxiv.org/rss/cs.AI",
            "trust_weight": 0.75,
        },
        {
            "kind": "api",
            "name": "Hacker News",
            "identifier": "https://hn.algolia.com/api/v1/search",
            "trust_weight": 0.45,
        },
    ],
    "brand_profiles": [],
}


@pytest.fixture
def workspace_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An isolated config directory and database for reconciliation tests."""
    config_dir = tmp_path / "config"
    (config_dir / "workspaces").mkdir(parents=True)
    path = config_dir / "workspaces" / "recon.yaml"
    path.write_text(yaml.safe_dump(BASE_CONFIG), encoding="utf-8")

    monkeypatch.setenv("ENGINE_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'recon.db'}")
    monkeypatch.setenv("ENGINE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENGINE_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("ENGINE_DEFAULT_WORKSPACE_SLUG", "recon")
    monkeypatch.setenv("COLUMNS", "240")
    get_settings.cache_clear()

    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
    yield path
    get_settings.cache_clear()


def write_config(path: Path, config: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(config), encoding="utf-8")


def active_sources() -> list[tuple[str, str, bool]]:
    container = build_container()
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug("recon")
        assert workspace is not None
        sources = uow.sources.list_for_workspace(workspace.id, only_active=False)
        return [(s.name, s.identifier, s.is_active) for s in sources]


class TestReconciliation:
    def test_seeding_twice_creates_nothing_new(self, workspace_config: Path) -> None:
        assert runner.invoke(app, ["workspace", "seed"]).exit_code == 0
        first = active_sources()

        result = runner.invoke(app, ["workspace", "seed"])
        assert result.exit_code == 0
        assert "0 created" in result.stdout
        assert active_sources() == first

    def test_changing_a_url_updates_in_place(self, workspace_config: Path) -> None:
        """The defect that motivated this: a URL change must not fork a source."""
        assert runner.invoke(app, ["workspace", "seed"]).exit_code == 0

        entries = [dict(entry) for entry in BASE_CONFIG["sources"]]
        entries[0]["identifier"] = "https://export.arxiv.org/rss/cs.AI"
        write_config(workspace_config, {**BASE_CONFIG, "sources": entries})

        result = runner.invoke(app, ["workspace", "seed"])
        assert result.exit_code == 0
        assert "0 created" in result.stdout

        sources = active_sources()
        assert len(sources) == 2
        arxiv = next(s for s in sources if s[0] == "arXiv cs.AI")
        assert arxiv[1] == "https://export.arxiv.org/rss/cs.AI"
        assert arxiv[2] is True

    def test_removing_a_source_deactivates_rather_than_deletes(
        self, workspace_config: Path
    ) -> None:
        """Ingested documents are evidence; a config edit must not destroy them."""
        assert runner.invoke(app, ["workspace", "seed"]).exit_code == 0

        config = {**BASE_CONFIG, "sources": [BASE_CONFIG["sources"][0]]}
        write_config(workspace_config, config)

        result = runner.invoke(app, ["workspace", "seed"])
        assert result.exit_code == 0
        assert "1 deactivated" in result.stdout

        sources = active_sources()
        assert len(sources) == 2  # still present, not deleted
        hacker_news = next(s for s in sources if s[0] == "Hacker News")
        assert hacker_news[2] is False

    def test_reinstating_a_source_reactivates_it(self, workspace_config: Path) -> None:
        assert runner.invoke(app, ["workspace", "seed"]).exit_code == 0
        write_config(workspace_config, {**BASE_CONFIG, "sources": [BASE_CONFIG["sources"][0]]})
        assert runner.invoke(app, ["workspace", "seed"]).exit_code == 0

        write_config(workspace_config, BASE_CONFIG)
        assert runner.invoke(app, ["workspace", "seed"]).exit_code == 0

        sources = active_sources()
        assert all(active for _, _, active in sources)
        assert len(sources) == 2

    def test_two_sources_may_share_an_endpoint(self, workspace_config: Path) -> None:
        """One HN query at two attention thresholds is a legitimate configuration."""
        config = {
            **BASE_CONFIG,
            "sources": [
                {
                    "kind": "api",
                    "name": "Hacker News (hot)",
                    "identifier": "https://hn.algolia.com/api/v1/search",
                    "config": {"filters": {"min": {"points": 500}}},
                },
                {
                    "kind": "api",
                    "name": "Hacker News (rising)",
                    "identifier": "https://hn.algolia.com/api/v1/search",
                    "config": {"filters": {"min": {"points": 50}}},
                },
            ],
        }
        write_config(workspace_config, config)

        result = runner.invoke(app, ["workspace", "seed"])
        assert result.exit_code == 0, result.stdout
        assert len(active_sources()) == 2

    def test_duplicate_names_in_config_are_rejected_by_the_database(
        self, workspace_config: Path
    ) -> None:
        """Name uniqueness is enforced by a constraint, not just by convention."""
        config = {
            **BASE_CONFIG,
            "sources": [
                {"kind": "rss", "name": "Same", "identifier": "https://a.example/feed"},
                {"kind": "rss", "name": "Same", "identifier": "https://b.example/feed"},
            ],
        }
        write_config(workspace_config, config)

        result = runner.invoke(app, ["workspace", "seed"])
        assert result.exit_code != 0
