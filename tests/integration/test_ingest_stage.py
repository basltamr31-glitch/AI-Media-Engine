"""Integration tests for the ingest stage, against a real database.

The headline guarantee -- running twice ingests once -- is meaningless against a
mocked repository, because the thing being tested *is* the interaction with
persistence: uniqueness constraints, flush visibility and transaction
boundaries. These run on real SQLite.
"""

from __future__ import annotations

import pytest

from engine.adapters.sources.registry import FetcherRegistry
from engine.core.domain.enums import RunStatus, SourceKind, StageStatus
from engine.core.domain.source import Source
from engine.core.ports.http import HttpError
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.base import StageError
from engine.pipeline.stages.ingest import IngestStage
from tests.fakes import FakeFetcher, RecordingArchive, make_document

pytestmark = pytest.mark.integration


@pytest.fixture
def rss_source(uow, persisted_workspace):
    source = Source(
        workspace_id=persisted_workspace.id,
        kind=SourceKind.RSS,
        name="arXiv cs.AI",
        identifier="http://export.arxiv.org/rss/cs.AI",
    )
    with uow as active:
        active.sources.add(source)
        active.commit()
    return source


@pytest.fixture
def api_source(uow, persisted_workspace):
    source = Source(
        workspace_id=persisted_workspace.id,
        kind=SourceKind.API,
        name="Hacker News",
        identifier="https://hn.algolia.com/api/v1/search",
    )
    with uow as active:
        active.sources.add(source)
        active.commit()
    return source


def documents_for(source, clock, count: int, *, prefix: str = "hash"):
    return [
        make_document(
            workspace_id=source.workspace_id,
            source_id=source.id,
            content_hash=f"{prefix}-{index}",
            fetched_at=clock.now(),
            title=f"Story {index}",
        )
        for index in range(count)
    ]


def run_stage(stage, uow, clock, settings, workspace_id, *, dry_run: bool = False):
    runner = PipelineRunner(uow=uow, clock=clock, settings=settings)
    return runner.run(
        workspace_id=workspace_id,
        pipeline="ingest",
        stages=[stage],
        dry_run=dry_run,
    )


class TestHappyPath:
    def test_stores_fetched_documents(self, uow, clock, settings, persisted_workspace, rss_source):
        registry = FetcherRegistry()
        registry.register(FakeFetcher(SourceKind.RSS, documents_for(rss_source, clock, 3)))
        archive = RecordingArchive()

        ledger = run_stage(
            IngestStage(fetchers=registry, archive=archive),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )

        assert ledger.status is RunStatus.SUCCEEDED
        with uow as active:
            assert active.documents.count_for_source(persisted_workspace.id, rss_source.id) == 3
        assert len(archive.stored) == 3

    def test_records_per_source_counts_in_the_ledger(
        self, uow, clock, settings, persisted_workspace, rss_source
    ):
        registry = FetcherRegistry()
        registry.register(FakeFetcher(SourceKind.RSS, documents_for(rss_source, clock, 2)))

        ledger = run_stage(
            IngestStage(fetchers=registry, archive=RecordingArchive()),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )

        with uow as active:
            stages = active.runs.list_stages(persisted_workspace.id, ledger.id)
        record = stages[0]
        assert record.status is StageStatus.SUCCEEDED
        assert record.items_in == 2
        assert record.items_out == 2
        assert record.meta["sources"]["arXiv cs.AI"]["stored"] == 2

    def test_ingests_from_multiple_transports_in_one_run(
        self, uow, clock, settings, persisted_workspace, rss_source, api_source
    ):
        registry = FetcherRegistry()
        rss_docs = documents_for(rss_source, clock, 2, prefix="r")
        api_docs = documents_for(api_source, clock, 3, prefix="a")
        registry.register(FakeFetcher(SourceKind.RSS, rss_docs))
        registry.register(FakeFetcher(SourceKind.API, api_docs))

        ledger = run_stage(
            IngestStage(fetchers=registry, archive=RecordingArchive()),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )

        assert ledger.status is RunStatus.SUCCEEDED
        with uow as active:
            stages = active.runs.list_stages(persisted_workspace.id, ledger.id)
        assert stages[0].items_out == 5


class TestIdempotency:
    def test_running_twice_stores_documents_once(
        self, uow, clock, settings, persisted_workspace, rss_source
    ):
        """The core Phase 1 guarantee."""
        documents = documents_for(rss_source, clock, 3)
        registry = FetcherRegistry()
        registry.register(FakeFetcher(SourceKind.RSS, documents))
        stage = IngestStage(fetchers=registry, archive=RecordingArchive())

        run_stage(stage, uow, clock, settings, persisted_workspace.id)
        second = run_stage(stage, uow, clock, settings, persisted_workspace.id)

        with uow as active:
            assert active.documents.count_for_source(persisted_workspace.id, rss_source.id) == 3
            stages = active.runs.list_stages(persisted_workspace.id, second.id)
        assert stages[0].items_in == 3
        assert stages[0].items_out == 0
        assert stages[0].meta["sources"]["arXiv cs.AI"]["duplicates"] == 3

    def test_only_genuinely_new_documents_are_added(
        self, uow, clock, settings, persisted_workspace, rss_source
    ):
        registry = FetcherRegistry()
        fetcher = FakeFetcher(SourceKind.RSS, documents_for(rss_source, clock, 2))
        registry.register(fetcher)
        stage = IngestStage(fetchers=registry, archive=RecordingArchive())
        run_stage(stage, uow, clock, settings, persisted_workspace.id)

        # The feed moves on: one document carries over, two are new.
        registry_two = FetcherRegistry()
        carried_over = documents_for(rss_source, clock, 1)
        fresh = documents_for(rss_source, clock, 2, prefix="new")
        registry_two.register(FakeFetcher(SourceKind.RSS, carried_over + fresh))

        ledger = run_stage(
            IngestStage(fetchers=registry_two, archive=RecordingArchive()),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )

        with uow as active:
            assert active.documents.count_for_source(persisted_workspace.id, rss_source.id) == 4
            stages = active.runs.list_stages(persisted_workspace.id, ledger.id)
        assert stages[0].items_out == 2

    def test_duplicates_within_a_single_batch_are_collapsed(
        self, uow, clock, settings, persisted_workspace, rss_source
    ):
        """Two sources can surface the same item; the DB check alone would miss it."""
        duplicated = documents_for(rss_source, clock, 1) * 3
        registry = FetcherRegistry()
        registry.register(FakeFetcher(SourceKind.RSS, duplicated))

        ledger = run_stage(
            IngestStage(fetchers=registry, archive=RecordingArchive()),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )

        with uow as active:
            assert active.documents.count_for_source(persisted_workspace.id, rss_source.id) == 1
            stages = active.runs.list_stages(persisted_workspace.id, ledger.id)
        assert stages[0].items_out == 1


class TestFaultIsolation:
    def test_one_failing_source_does_not_stop_the_others(
        self, uow, clock, settings, persisted_workspace, rss_source, api_source
    ):
        registry = FetcherRegistry()
        registry.register(FakeFetcher(SourceKind.RSS, error=HttpError("feed", "connection reset")))
        registry.register(FakeFetcher(SourceKind.API, documents_for(api_source, clock, 2)))

        ledger = run_stage(
            IngestStage(fetchers=registry, archive=RecordingArchive()),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )

        assert ledger.status is RunStatus.SUCCEEDED
        with uow as active:
            assert active.documents.count_for_source(persisted_workspace.id, api_source.id) == 2
            stages = active.runs.list_stages(persisted_workspace.id, ledger.id)
        sources = stages[0].meta["sources"]
        assert "connection reset" in sources["arXiv cs.AI"]["error"]
        assert sources["Hacker News"]["stored"] == 2
        assert stages[0].meta["failed_sources"] == 1

    def test_every_source_failing_fails_the_run(
        self, uow, clock, settings, persisted_workspace, rss_source
    ):
        """Total failure is systemic -- credentials, network, config -- and must be loud."""
        registry = FetcherRegistry()
        registry.register(FakeFetcher(SourceKind.RSS, error=HttpError("feed", "dns failure")))

        with pytest.raises(StageError, match="all 1 source"):
            run_stage(
                IngestStage(fetchers=registry, archive=RecordingArchive()),
                uow,
                clock,
                settings,
                persisted_workspace.id,
            )

        with uow as active:
            runs = active.runs.list_recent(persisted_workspace.id, limit=1)
        assert runs[0].status is RunStatus.FAILED

    def test_missing_fetcher_is_reported_not_fatal(
        self, uow, clock, settings, persisted_workspace, rss_source, api_source
    ):
        registry = FetcherRegistry()
        registry.register(FakeFetcher(SourceKind.API, documents_for(api_source, clock, 1)))

        ledger = run_stage(
            IngestStage(fetchers=registry, archive=RecordingArchive()),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )

        with uow as active:
            stages = active.runs.list_stages(persisted_workspace.id, ledger.id)
        assert "no fetcher registered" in stages[0].meta["sources"]["arXiv cs.AI"]["error"]
        assert ledger.status is RunStatus.SUCCEEDED


class TestOperationalModes:
    def test_dry_run_fetches_without_storing(
        self, uow, clock, settings, persisted_workspace, rss_source
    ):
        registry = FetcherRegistry()
        registry.register(FakeFetcher(SourceKind.RSS, documents_for(rss_source, clock, 3)))
        archive = RecordingArchive()

        ledger = run_stage(
            IngestStage(fetchers=registry, archive=archive),
            uow,
            clock,
            settings,
            persisted_workspace.id,
            dry_run=True,
        )

        with uow as active:
            assert active.documents.count_for_source(persisted_workspace.id, rss_source.id) == 0
            stages = active.runs.list_stages(persisted_workspace.id, ledger.id)
        assert stages[0].items_in == 3
        assert stages[0].items_out == 0
        assert archive.stored == []

    def test_only_source_filters_to_one_feed(
        self, uow, clock, settings, persisted_workspace, rss_source, api_source
    ):
        registry = FetcherRegistry()
        registry.register(FakeFetcher(SourceKind.RSS, documents_for(rss_source, clock, 2)))
        registry.register(FakeFetcher(SourceKind.API, documents_for(api_source, clock, 5)))

        ledger = run_stage(
            IngestStage(fetchers=registry, archive=RecordingArchive(), only_source="Hacker News"),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )

        with uow as active:
            assert active.documents.count_for_source(persisted_workspace.id, rss_source.id) == 0
            assert active.documents.count_for_source(persisted_workspace.id, api_source.id) == 5
            stages = active.runs.list_stages(persisted_workspace.id, ledger.id)
        assert stages[0].meta["source_count"] == 1

    def test_workspace_with_no_sources_is_skipped_not_failed(
        self, uow, clock, settings, persisted_workspace
    ):
        ledger = run_stage(
            IngestStage(fetchers=FetcherRegistry(), archive=RecordingArchive()),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )

        assert ledger.status is RunStatus.SUCCEEDED
        with uow as active:
            stages = active.runs.list_stages(persisted_workspace.id, ledger.id)
        assert stages[0].status is StageStatus.SKIPPED
        assert "no active sources" in stages[0].meta["skip_reason"]

    def test_inactive_sources_are_ignored(
        self, uow, clock, settings, persisted_workspace, rss_source
    ):
        rss_source.is_active = False
        with uow as active:
            active.sources.update(rss_source)
            active.commit()

        registry = FetcherRegistry()
        registry.register(FakeFetcher(SourceKind.RSS, documents_for(rss_source, clock, 3)))

        ledger = run_stage(
            IngestStage(fetchers=registry, archive=RecordingArchive()),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )

        with uow as active:
            stages = active.runs.list_stages(persisted_workspace.id, ledger.id)
        assert stages[0].status is StageStatus.SKIPPED
