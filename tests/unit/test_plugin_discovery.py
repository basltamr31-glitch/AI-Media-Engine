"""Tests for entry-point plugin discovery (ADR 0017).

The property that matters is **isolation**: a third-party package that fails to
import, blows up in its factory, returns the wrong type or collides with a key
already taken must cost exactly itself. If any of these tests start failing,
one bad plugin can take down a customer's pipeline.

Entry points are constructed directly rather than installed, so these tests say
nothing about whether the *built-in* plugins are declared correctly -- that is
what `tests/integration/test_cli_plugins.py` covers, against the real installed
metadata.
"""

from __future__ import annotations

from datetime import UTC, datetime
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest

from engine.adapters.clock import FixedClock
from engine.adapters.plugins.context import PluginContext
from engine.adapters.plugins.discovery import (
    GROUP_FETCHERS,
    PluginLoader,
    PluginStatus,
)
from engine.adapters.sources.registry import FetcherRegistry
from engine.core.domain.enums import SourceKind
from engine.core.domain.source import Source, SourceDocument
from engine.core.ports.sources import SourceFetcher
from engine.settings import Settings

# --------------------------------------------------------------------------
# Plugins under test. Module-level so entry points can name them by dotted path.
# --------------------------------------------------------------------------


class StubFetcher:
    """A minimal fetcher for an arbitrary transport."""

    def __init__(self, kind: SourceKind) -> None:
        self._kind = kind

    @property
    def kind(self) -> SourceKind:
        return self._kind

    def fetch(self, source: Source, *, limit: int = 50) -> list[SourceDocument]:
        return []


def build_rss(context: PluginContext) -> StubFetcher:
    assert context.settings is not None
    return StubFetcher(SourceKind.RSS)


def build_api(context: PluginContext) -> StubFetcher:
    return StubFetcher(SourceKind.API)


def build_duplicate_rss(context: PluginContext) -> StubFetcher:
    return StubFetcher(SourceKind.RSS)


def build_exploding(context: PluginContext) -> StubFetcher:
    msg = "third-party plugin is unhappy"
    raise RuntimeError(msg)


def build_wrong_type(context: PluginContext) -> str:
    return "not a fetcher"


HERE = "tests.unit.test_plugin_discovery"


@pytest.fixture
def context(tmp_path: Path) -> PluginContext:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    return PluginContext(
        settings=settings,
        clock=FixedClock(datetime(2026, 1, 15, 9, 30, tzinfo=UTC)),
        http=None,  # type: ignore[arg-type]  # unused by these stubs
        resolve_path=lambda path: path if path.is_absolute() else tmp_path / path,
    )


def loader_over(context: PluginContext, **entry_points: str) -> PluginLoader:
    """A loader whose entry points are supplied rather than installed."""
    points = [
        EntryPoint(name=name, value=value, group=GROUP_FETCHERS)
        for name, value in entry_points.items()
    ]
    return PluginLoader(context, source=lambda group: points)


def load_fetchers(loader: PluginLoader) -> FetcherRegistry:
    registry = FetcherRegistry()
    loader.load(GROUP_FETCHERS, expects=SourceFetcher, register=registry.register)
    return registry


class TestSuccessfulDiscovery:
    def test_a_declared_plugin_is_constructed_and_registered(self, context: PluginContext):
        loader = loader_over(context, rss=f"{HERE}:build_rss")
        registry = load_fetchers(loader)

        assert registry.keys() == ["rss"]
        assert loader.records[0].status is PluginStatus.LOADED

    def test_the_factory_receives_the_context(self, context: PluginContext):
        """`build_rss` asserts on what it is handed; a bare call would not."""
        loader = loader_over(context, rss=f"{HERE}:build_rss")
        assert len(load_fetchers(loader)) == 1

    def test_plugins_load_in_name_order(self, context: PluginContext):
        """Registration order must not depend on installation order."""
        loader = loader_over(context, rss=f"{HERE}:build_rss", api=f"{HERE}:build_api")
        load_fetchers(loader)
        assert [record.name for record in loader.records] == ["api", "rss"]

    def test_a_synthetic_entry_point_reports_an_unknown_provider(self, context: PluginContext):
        """Provenance is reported honestly rather than guessed at."""
        loader = loader_over(context, rss=f"{HERE}:build_rss")
        load_fetchers(loader)
        assert loader.records[0].distribution == "unknown"

    def test_an_empty_group_is_not_an_error(self, context: PluginContext):
        """The engine ships no parsers until Phase 2."""
        loader = loader_over(context)
        assert len(load_fetchers(loader)) == 0
        assert loader.records == []


class TestFailureIsolation:
    """One broken package must not stop a customer's pipeline."""

    def test_a_factory_that_raises_is_skipped_not_propagated(self, context: PluginContext):
        loader = loader_over(context, broken=f"{HERE}:build_exploding")
        registry = load_fetchers(loader)

        assert len(registry) == 0
        record = loader.records[0]
        assert record.failed
        assert "third-party plugin is unhappy" in (record.error or "")

    def test_a_broken_plugin_does_not_prevent_the_others_loading(self, context: PluginContext):
        loader = loader_over(
            context,
            broken=f"{HERE}:build_exploding",
            rss=f"{HERE}:build_rss",
            api=f"{HERE}:build_api",
        )
        registry = load_fetchers(loader)

        assert registry.keys() == ["api", "rss"]
        assert [record.name for record in loader.records if record.failed] == ["broken"]

    def test_an_unimportable_entry_point_is_recorded(self, context: PluginContext):
        loader = loader_over(context, ghost="engine_nonexistent.module:build")
        assert len(load_fetchers(loader)) == 0
        assert loader.records[0].failed

    def test_a_missing_attribute_is_recorded(self, context: PluginContext):
        loader = loader_over(context, ghost=f"{HERE}:build_nothing")
        assert len(load_fetchers(loader)) == 0
        assert loader.records[0].failed

    def test_the_wrong_type_fails_at_startup_not_mid_run(self, context: PluginContext):
        """A registry holding a non-fetcher would explode during ingestion."""
        loader = loader_over(context, bogus=f"{HERE}:build_wrong_type")
        registry = load_fetchers(loader)

        assert len(registry) == 0
        assert "does not implement SourceFetcher" in (loader.records[0].error or "")

    def test_a_key_collision_costs_only_the_second_plugin(self, context: PluginContext):
        """Silently overwriting would make fetcher selection order-dependent."""
        loader = loader_over(
            context,
            arss=f"{HERE}:build_rss",
            zrss=f"{HERE}:build_duplicate_rss",
        )
        registry = load_fetchers(loader)

        assert registry.keys() == ["rss"]
        statuses = {record.name: record.status for record in loader.records}
        assert statuses == {"arss": PluginStatus.LOADED, "zrss": PluginStatus.FAILED}
        assert "already registered" in (loader.records_for(GROUP_FETCHERS)[1].error or "")


class TestRecords:
    def test_records_are_grouped_and_queryable(self, context: PluginContext):
        loader = loader_over(context, rss=f"{HERE}:build_rss")
        load_fetchers(loader)

        assert loader.records_for(GROUP_FETCHERS) == loader.records
        assert loader.records_for("engine.parsers") == []

    def test_a_record_describes_where_the_plugin_came_from(self, context: PluginContext):
        loader = loader_over(context, rss=f"{HERE}:build_rss")
        load_fetchers(loader)

        record = loader.records[0]
        assert record.group == GROUP_FETCHERS
        assert record.name == "rss"
        assert record.value == f"{HERE}:build_rss"
        assert record.provider == "unknown"
