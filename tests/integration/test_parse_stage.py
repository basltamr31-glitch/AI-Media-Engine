"""Integration tests for the parse stage, against a real database.

The guarantees under test -- running twice extracts once, one bad document does
not stop the rest, ``--reparse`` replaces rather than duplicates -- are all
about the interaction with persistence, so these run on real SQLite rather than
against a mocked repository.
"""

from __future__ import annotations

from typing import Any

import pytest

from engine.adapters.sources.registry import ParserRegistry
from engine.core.domain.enums import RunStatus, SourceKind
from engine.core.domain.source import Source
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.base import StageError
from engine.pipeline.stages.parse import ParseStage
from tests.fakes import FakeParser, make_document

pytestmark = pytest.mark.integration


@pytest.fixture
def email_source(uow, persisted_workspace):
    source = Source(
        workspace_id=persisted_workspace.id,
        kind=SourceKind.EMAIL,
        name="TLDR AI",
        identifier="dan@tldrnewsletter.com",
        parser_key="tldr_newsletter",
        config={"strip_sections": ["sponsor"]},
    )
    with uow as active:
        active.sources.add(source)
        active.commit()
    return source


@pytest.fixture
def unparseable_source(uow, persisted_workspace):
    """A source whose parser has not been written yet -- arXiv, until 2.2."""
    source = Source(
        workspace_id=persisted_workspace.id,
        kind=SourceKind.RSS,
        name="arXiv cs.AI",
        identifier="https://export.arxiv.org/rss/cs.AI",
        parser_key="arxiv_rss",
    )
    with uow as active:
        active.sources.add(source)
        active.commit()
    return source


def store_documents(uow, source, clock, count: int):
    documents = [
        make_document(
            workspace_id=source.workspace_id,
            source_id=source.id,
            content_hash=f"{source.id}-{index}",
            fetched_at=clock.now(),
            title=f"Issue {index}",
        )
        for index in range(count)
    ]
    with uow as active:
        for document in documents:
            active.documents.add(document)
        active.commit()
    return documents


def registry_with(parser) -> ParserRegistry:
    registry = ParserRegistry()
    registry.register(parser)
    return registry


def run_stage(stage, uow, clock, settings, workspace_id, *, dry_run: bool = False):
    runner = PipelineRunner(uow=uow, clock=clock, settings=settings)
    return runner.run(
        workspace_id=workspace_id,
        pipeline="parse",
        stages=[stage],
        dry_run=dry_run,
    )


def stage_meta(uow, workspace_id, ledger) -> dict[str, Any]:
    with uow as active:
        records = active.runs.list_stages(workspace_id, ledger.id)
    return next(record.meta for record in records if record.stage == "parse")


class TestHappyPath:
    def test_candidates_are_stored(self, uow, clock, settings, persisted_workspace, email_source):
        store_documents(uow, email_source, clock, 2)

        ledger = run_stage(
            ParseStage(parsers=registry_with(FakeParser("tldr_newsletter", per_document=3))),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )

        assert ledger.status is RunStatus.SUCCEEDED
        with uow as active:
            assert active.candidates.count_for_workspace(persisted_workspace.id) == 6

    def test_the_source_config_reaches_the_parser(
        self, uow, clock, settings, persisted_workspace, email_source
    ):
        """ "Adding a newsletter is a config row" has to hold for parsing too."""
        store_documents(uow, email_source, clock, 1)
        parser = FakeParser("tldr_newsletter", per_document=1)

        run_stage(
            ParseStage(parsers=registry_with(parser)),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )

        assert parser.configs == [{"strip_sections": ["sponsor"]}]

    def test_per_source_counts_land_in_the_ledger(
        self, uow, clock, settings, persisted_workspace, email_source
    ):
        store_documents(uow, email_source, clock, 2)

        ledger = run_stage(
            ParseStage(parsers=registry_with(FakeParser("tldr_newsletter", per_document=2))),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )

        stats = stage_meta(uow, persisted_workspace.id, ledger)["sources"]["TLDR AI"]
        assert stats == {
            "parser": "tldr_newsletter",
            "documents": 2,
            "parsed": 2,
            "candidates": 4,
            "already_parsed": 0,
            "unparseable": 0,
            "failed": 0,
            "error": None,
        }

    def test_a_dry_run_stores_nothing(
        self, uow, clock, settings, persisted_workspace, email_source
    ):
        store_documents(uow, email_source, clock, 2)

        ledger = run_stage(
            ParseStage(parsers=registry_with(FakeParser("tldr_newsletter", per_document=3))),
            uow,
            clock,
            settings,
            persisted_workspace.id,
            dry_run=True,
        )

        assert ledger.status is RunStatus.SUCCEEDED
        with uow as active:
            assert active.candidates.count_for_workspace(persisted_workspace.id) == 0

    def test_only_one_source_can_be_parsed(
        self, uow, clock, settings, persisted_workspace, email_source, unparseable_source
    ):
        store_documents(uow, email_source, clock, 1)
        store_documents(uow, unparseable_source, clock, 1)

        ledger = run_stage(
            ParseStage(
                parsers=registry_with(FakeParser("tldr_newsletter", per_document=1)),
                only_source="TLDR AI",
            ),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )

        assert set(stage_meta(uow, persisted_workspace.id, ledger)["sources"]) == {"TLDR AI"}


class TestIdempotency:
    def test_running_twice_extracts_once(
        self, uow, clock, settings, persisted_workspace, email_source
    ):
        store_documents(uow, email_source, clock, 2)
        parser = FakeParser("tldr_newsletter", per_document=3)

        for _ in range(2):
            run_stage(
                ParseStage(parsers=registry_with(parser)),
                uow,
                clock,
                settings,
                persisted_workspace.id,
            )

        with uow as active:
            assert active.candidates.count_for_workspace(persisted_workspace.id) == 6
        assert parser.calls == 2  # the second run parsed nothing

    def test_the_second_run_reports_what_it_skipped(
        self, uow, clock, settings, persisted_workspace, email_source
    ):
        store_documents(uow, email_source, clock, 2)
        parser = FakeParser("tldr_newsletter", per_document=1)
        stage = ParseStage(parsers=registry_with(parser))

        run_stage(stage, uow, clock, settings, persisted_workspace.id)
        ledger = run_stage(stage, uow, clock, settings, persisted_workspace.id)

        stats = stage_meta(uow, persisted_workspace.id, ledger)["sources"]["TLDR AI"]
        assert stats["already_parsed"] == 2
        assert stats["parsed"] == 0

    def test_reparse_replaces_rather_than_duplicates(
        self, uow, clock, settings, persisted_workspace, email_source
    ):
        """Candidates are derived data: a fixed parser should be able to redo them."""
        store_documents(uow, email_source, clock, 1)

        run_stage(
            ParseStage(parsers=registry_with(FakeParser("tldr_newsletter", per_document=3))),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )
        run_stage(
            ParseStage(
                parsers=registry_with(FakeParser("tldr_newsletter", per_document=2)),
                reparse=True,
            ),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )

        with uow as active:
            assert active.candidates.count_for_workspace(persisted_workspace.id) == 2

    def test_a_new_document_is_parsed_while_old_ones_are_left_alone(
        self, uow, clock, settings, persisted_workspace, email_source
    ):
        store_documents(uow, email_source, clock, 1)
        stage = ParseStage(parsers=registry_with(FakeParser("tldr_newsletter", per_document=2)))
        run_stage(stage, uow, clock, settings, persisted_workspace.id)

        with uow as active:
            active.documents.add(
                make_document(
                    workspace_id=email_source.workspace_id,
                    source_id=email_source.id,
                    content_hash="later",
                    fetched_at=clock.now(),
                    title="Tomorrow's issue",
                )
            )
            active.commit()

        run_stage(stage, uow, clock, settings, persisted_workspace.id)

        with uow as active:
            assert active.candidates.count_for_workspace(persisted_workspace.id) == 4


class TestFaultIsolation:
    def test_one_failing_document_does_not_stop_the_others(
        self, uow, clock, settings, persisted_workspace, email_source
    ):
        """Newsletter templates change without warning."""
        store_documents(uow, email_source, clock, 3)
        parser = FakeParser("tldr_newsletter", per_document=2, fail_on_title="Issue 1")

        ledger = run_stage(
            ParseStage(parsers=registry_with(parser)),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )

        assert ledger.status is RunStatus.SUCCEEDED
        stats = stage_meta(uow, persisted_workspace.id, ledger)["sources"]["TLDR AI"]
        assert stats["parsed"] == 2
        assert stats["failed"] == 1
        assert "template changed" in stats["error"]
        with uow as active:
            assert active.candidates.count_for_workspace(persisted_workspace.id) == 4

    def test_a_missing_parser_is_reported_not_raised(
        self, uow, clock, settings, persisted_workspace, email_source, unparseable_source
    ):
        """arXiv has no parser until 2.2; that must not fail the run."""
        store_documents(uow, email_source, clock, 1)
        store_documents(uow, unparseable_source, clock, 1)

        ledger = run_stage(
            ParseStage(parsers=registry_with(FakeParser("tldr_newsletter", per_document=1))),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )

        assert ledger.status is RunStatus.SUCCEEDED
        stats = stage_meta(uow, persisted_workspace.id, ledger)["sources"]
        assert "no parser registered for 'arxiv_rss'" in stats["arXiv cs.AI"]["error"]

    def test_a_declined_document_is_counted_not_failed(
        self, uow, clock, settings, persisted_workspace, email_source
    ):
        store_documents(uow, email_source, clock, 2)
        parser = FakeParser("tldr_newsletter", per_document=1, accepts=False)

        ledger = run_stage(
            ParseStage(parsers=registry_with(parser)),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )

        stats = stage_meta(uow, persisted_workspace.id, ledger)["sources"]["TLDR AI"]
        assert stats["unparseable"] == 2
        assert stats["failed"] == 0

    def test_every_document_failing_fails_the_run(
        self, uow, clock, settings, persisted_workspace, email_source
    ):
        """Total failure is a bad deployment, not a template change."""
        store_documents(uow, email_source, clock, 2)
        parser = FakeParser("tldr_newsletter", per_document=1, fail_on_title="Issue")

        with pytest.raises(StageError, match="every document failed"):
            run_stage(
                ParseStage(parsers=registry_with(parser)),
                uow,
                clock,
                settings,
                persisted_workspace.id,
            )


class TestNothingToDo:
    def test_no_documents_is_a_skip_not_a_failure(
        self, uow, clock, settings, persisted_workspace, email_source
    ):
        ledger = run_stage(
            ParseStage(parsers=registry_with(FakeParser("tldr_newsletter"))),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )
        assert ledger.status is RunStatus.SUCCEEDED

    def test_a_source_without_a_parser_key_is_not_considered(
        self, uow, clock, settings, persisted_workspace
    ):
        """Its documents must not consume the document budget."""
        source = Source(
            workspace_id=persisted_workspace.id,
            kind=SourceKind.RSS,
            name="Unconfigured feed",
            identifier="https://example.test/feed.xml",
        )
        with uow as active:
            active.sources.add(source)
            active.commit()
        store_documents(uow, source, clock, 3)

        ledger = run_stage(
            ParseStage(parsers=registry_with(FakeParser("tldr_newsletter"))),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )

        assert stage_meta(uow, persisted_workspace.id, ledger).get("skip_reason") is not None

    def test_an_unknown_source_name_is_a_skip(
        self, uow, clock, settings, persisted_workspace, email_source
    ):
        ledger = run_stage(
            ParseStage(
                parsers=registry_with(FakeParser("tldr_newsletter")), only_source="Ghost feed"
            ),
            uow,
            clock,
            settings,
            persisted_workspace.id,
        )
        assert ledger.status is RunStatus.SUCCEEDED
