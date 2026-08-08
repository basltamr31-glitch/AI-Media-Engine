"""End-to-end tests for plugin discovery against the real installed metadata.

These are the ones that catch a broken ``pyproject.toml``. The unit tests inject
synthetic entry points and so would keep passing if every declaration in the
project file were deleted; here nothing is injected, and the engine's own three
fetchers have to arrive through exactly the path a third-party package uses.

If this file fails right after editing entry points, re-run::

    pip install -e . --no-deps

Entry points live in the installed metadata, not in the source tree.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from engine.adapters.plugins.discovery import GROUP_FETCHERS, GROUP_PARSERS, PluginStatus
from engine.cli.container import build_container
from engine.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture(autouse=True)
def wide_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "240")


class TestBuiltInsArePlugins:
    """ADR 0017's first rule: if discovery breaks, it breaks in development."""

    def test_every_built_in_fetcher_is_discovered(self) -> None:
        registry = build_container().fetchers
        assert registry.keys() == ["api", "email", "rss"]

    def test_they_are_declared_by_this_distribution(self) -> None:
        records = build_container().plugin_records()
        fetchers = [record for record in records if record.group == GROUP_FETCHERS]

        assert {record.name for record in fetchers} == {"api", "email", "rss"}
        assert all(record.distribution == "ai-media-engine" for record in fetchers)
        assert all(record.status is PluginStatus.LOADED for record in fetchers)

    def test_the_gmail_fetcher_loads_without_google_credentials(self) -> None:
        """Discovery must not require an authenticated machine.

        The credential store is built eagerly but read lazily; if that ever
        inverts, a workspace with only RSS sources stops working on a machine
        that has never run `engine sources auth`.
        """
        assert "email" in build_container().fetchers

    def test_the_parser_group_is_discovered_the_same_way(self) -> None:
        """Phase 2's parser is a plugin, not a special case in the container."""
        container = build_container()
        assert "tldr_newsletter" in container.parsers

        # Records exist only once the registry that consumes them is built --
        # inspection reports what the engine actually loaded, not a parallel pass.
        records = container.plugin_loader.records_for(GROUP_PARSERS)
        assert [record.name for record in records] == [
            "arxiv_rss",
            "hackernews_api",
            "tldr_newsletter",
        ]
        assert all(record.distribution == "ai-media-engine" for record in records)


class TestPluginsList:
    def test_list_reports_the_built_in_fetchers(self) -> None:
        result = runner.invoke(app, ["plugins", "list"])
        assert result.exit_code == 0, result.stdout
        for name in ("api", "email", "rss"):
            assert name in result.stdout
        assert "ai-media-engine" in result.stdout
        assert "loaded" in result.stdout

    def test_list_can_be_filtered_by_group(self) -> None:
        result = runner.invoke(app, ["plugins", "list", "--group", "fetchers"])
        assert result.exit_code == 0
        assert "rss" in result.stdout

    def test_the_full_group_name_works_too(self) -> None:
        result = runner.invoke(app, ["plugins", "list", "--group", GROUP_FETCHERS])
        assert result.exit_code == 0
        assert "rss" in result.stdout

    def test_an_unknown_group_says_so_and_lists_the_known_ones(self) -> None:
        result = runner.invoke(app, ["plugins", "list", "--group", "renderers"])
        assert result.exit_code == 0
        assert "No plugins found" in result.stdout
        assert GROUP_FETCHERS in result.stdout
