"""Integration tests for the evidence stage.

Two things are being protected. First, that reconciliation is idempotent --
this stage runs on every pipeline pass, and evidence that grew a duplicate row
each time would be worthless as a ledger. Second, and more important, that
nothing is called ``primary`` unless a source said so: over-claiming
provenance is exactly the failure the ledger exists to prevent.
"""

from __future__ import annotations

import pytest

from engine.core.domain.enums import EvidenceKind, RunStatus, SourceKind, StoryStatus
from engine.core.domain.ledger import RunLedger
from engine.core.domain.source import Source, SourceDocument, compute_content_hash
from engine.core.domain.story import EnrichedSource, Evidence, Story, StoryCandidate
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.evidence import EvidenceStage

pytestmark = pytest.mark.integration


@pytest.fixture
def newsletter(uow, persisted_workspace):
    return _source(uow, persisted_workspace, "TLDR AI", SourceKind.EMAIL, "dan@tldrnewsletter.com")


@pytest.fixture
def journal(uow, persisted_workspace):
    """A source that publishes what it links to, and says so."""
    return _source(
        uow,
        persisted_workspace,
        "arXiv cs.AI",
        SourceKind.RSS,
        "https://export.arxiv.org/rss/cs.AI",
        config={"evidence_kind": "primary"},
    )


@pytest.fixture
def publisher(uow, persisted_workspace):
    """A source whose links point at its own site, with nothing declared."""
    return _source(
        uow, persisted_workspace, "TechCrunch", SourceKind.RSS, "https://techcrunch.com/feed"
    )


def _source(uow, workspace, name: str, kind: SourceKind, identifier: str, *, config=None) -> Source:
    source = Source(
        workspace_id=workspace.id,
        kind=kind,
        name=name,
        identifier=identifier,
        config=config or {},
    )
    with uow as active:
        active.sources.add(source)
        active.commit()
    return source


def story_with(uow, workspace, clock, source, *, url: str, headline: str = "A story") -> Story:
    """One story, one mention of it, both persisted."""
    story = Story(
        workspace_id=workspace.id,
        canonical_headline=headline,
        primary_url=url,
        status=StoryStatus.RESOLVED,
        first_seen_at=clock.now(),
        last_seen_at=clock.now(),
    )
    document = SourceDocument(
        workspace_id=workspace.id,
        source_id=source.id,
        content_hash=compute_content_hash(headline, source.id),
        raw_content="body",
    )
    candidate = StoryCandidate(
        workspace_id=workspace.id,
        document_id=document.id,
        source_id=source.id,
        story_id=story.id,
        headline=headline,
        primary_url=url,
        extracted_at=clock.now(),
    )
    with uow as active:
        active.stories.add(story)
        active.documents.add(document)
        active.candidates.add_many([candidate])
        active.commit()
    return story


def enrich(uow, story, clock, *, url: str, title: str = "The article") -> None:
    story.enrichment = EnrichedSource(
        url=url, title=title, text="The announcement in full.", fetched_at=clock.now()
    )
    story.status = StoryStatus.ENRICHED
    with uow as active:
        active.stories.update(story)
        active.commit()


def run_stage(uow, clock, settings, workspace_id: str, *, dry_run: bool = False) -> RunLedger:
    runner = PipelineRunner(uow=uow, clock=clock, settings=settings)
    return runner.run(
        workspace_id=workspace_id,
        pipeline="evidence",
        stages=[EvidenceStage()],
        dry_run=dry_run,
    )


def evidence_for(uow, workspace_id: str, story_id: str) -> list[Evidence]:
    with uow as active:
        records: list[Evidence] = active.evidence.list_for_story(workspace_id, story_id)
    return records


class TestProvenance:
    def test_a_newsletter_mention_is_aggregator_evidence(
        self, uow, clock, settings, persisted_workspace, newsletter
    ):
        """It pointed at somebody else's work, which is all it did."""
        story = story_with(
            uow, persisted_workspace, clock, newsletter, url="https://techcrunch.com/a"
        )

        run_stage(uow, clock, settings, persisted_workspace.id)

        records = evidence_for(uow, persisted_workspace.id, story.id)
        assert [record.kind for record in records] == [EvidenceKind.AGGREGATOR]
        assert records[0].source_title == "TLDR AI"

    def test_a_source_linking_to_itself_is_secondary_by_default(
        self, uow, clock, settings, persisted_workspace, publisher
    ):
        """It published something -- but a URL cannot say it was the announcement."""
        story = story_with(
            uow, persisted_workspace, clock, publisher, url="https://techcrunch.com/a"
        )

        run_stage(uow, clock, settings, persisted_workspace.id)

        assert evidence_for(uow, persisted_workspace.id, story.id)[0].kind is (
            EvidenceKind.SECONDARY
        )

    def test_primary_is_only_ever_declared(
        self, uow, clock, settings, persisted_workspace, journal
    ):
        """arXiv publishes the authors' own words; config is how we know."""
        story = story_with(uow, persisted_workspace, clock, journal, url="https://arxiv.org/abs/1")

        run_stage(uow, clock, settings, persisted_workspace.id)

        assert evidence_for(uow, persisted_workspace.id, story.id)[0].kind is EvidenceKind.PRIMARY

    def test_an_unrecognised_declaration_falls_back_rather_than_failing(
        self, uow, clock, settings, persisted_workspace
    ):
        source = _source(
            uow,
            persisted_workspace,
            "Odd feed",
            SourceKind.RSS,
            "https://odd.test/feed",
            config={"evidence_kind": "gospel"},
        )
        story = story_with(uow, persisted_workspace, clock, source, url="https://elsewhere.test/a")

        run_stage(uow, clock, settings, persisted_workspace.id)

        assert evidence_for(uow, persisted_workspace.id, story.id)[0].kind is (
            EvidenceKind.AGGREGATOR
        )

    def test_the_claim_is_the_story_headline(
        self, uow, clock, settings, persisted_workspace, newsletter
    ):
        story = story_with(
            uow,
            persisted_workspace,
            clock,
            newsletter,
            url="https://techcrunch.com/a",
            headline="OpenAI cuts prices",
        )

        run_stage(uow, clock, settings, persisted_workspace.id)

        assert evidence_for(uow, persisted_workspace.id, story.id)[0].claim == "OpenAI cuts prices"

    def test_only_mentions_with_links_produce_evidence(
        self, uow, clock, settings, persisted_workspace, newsletter
    ):
        """Evidence has to point somewhere to be evidence."""
        story = story_with(uow, persisted_workspace, clock, newsletter, url="https://x.test/a")
        with uow as active:
            mention = active.candidates.list_for_story(persisted_workspace.id, story.id)[0]
            assert mention.primary_url

        run_stage(uow, clock, settings, persisted_workspace.id)
        assert len(evidence_for(uow, persisted_workspace.id, story.id)) == 1


class TestEnrichment:
    def test_reading_a_page_adds_evidence(
        self, uow, clock, settings, persisted_workspace, newsletter
    ):
        story = story_with(
            uow, persisted_workspace, clock, newsletter, url="https://techcrunch.com/a"
        )
        enrich(uow, story, clock, url="https://techcrunch.com/a")

        run_stage(uow, clock, settings, persisted_workspace.id)

        kinds = {record.kind for record in evidence_for(uow, persisted_workspace.id, story.id)}
        assert kinds == {EvidenceKind.AGGREGATOR, EvidenceKind.SECONDARY}

    def test_reading_a_declared_primary_source_stays_primary(
        self, uow, clock, settings, persisted_workspace, journal
    ):
        story = story_with(uow, persisted_workspace, clock, journal, url="https://arxiv.org/abs/1")
        enrich(uow, story, clock, url="https://arxiv.org/abs/1")

        run_stage(uow, clock, settings, persisted_workspace.id)

        kinds = {record.kind for record in evidence_for(uow, persisted_workspace.id, story.id)}
        assert kinds == {EvidenceKind.PRIMARY}

    def test_reading_a_page_never_promotes_an_aggregator(
        self, uow, clock, settings, persisted_workspace, newsletter
    ):
        """Having read a news report does not turn it into an announcement."""
        story = story_with(
            uow, persisted_workspace, clock, newsletter, url="https://techcrunch.com/a"
        )
        enrich(uow, story, clock, url="https://techcrunch.com/a")

        run_stage(uow, clock, settings, persisted_workspace.id)

        records = evidence_for(uow, persisted_workspace.id, story.id)
        assert not any(record.kind is EvidenceKind.PRIMARY for record in records)


class TestReconciliation:
    def test_running_twice_records_nothing_new(
        self, uow, clock, settings, persisted_workspace, newsletter
    ):
        story = story_with(uow, persisted_workspace, clock, newsletter, url="https://x.test/a")

        run_stage(uow, clock, settings, persisted_workspace.id)
        ledger = run_stage(uow, clock, settings, persisted_workspace.id)

        assert ledger.status is RunStatus.SUCCEEDED
        assert len(evidence_for(uow, persisted_workspace.id, story.id)) == 1

    def test_new_support_is_added_without_touching_the_rest(
        self, uow, clock, settings, persisted_workspace, newsletter
    ):
        story = story_with(uow, persisted_workspace, clock, newsletter, url="https://x.test/a")
        run_stage(uow, clock, settings, persisted_workspace.id)

        enrich(uow, story, clock, url="https://x.test/a")
        run_stage(uow, clock, settings, persisted_workspace.id)

        assert len(evidence_for(uow, persisted_workspace.id, story.id)) == 2

    def test_a_dry_run_records_nothing(self, uow, clock, settings, persisted_workspace, newsletter):
        story = story_with(uow, persisted_workspace, clock, newsletter, url="https://x.test/a")

        run_stage(uow, clock, settings, persisted_workspace.id, dry_run=True)

        assert evidence_for(uow, persisted_workspace.id, story.id) == []

    def test_nothing_to_reconcile_is_a_skip(self, uow, clock, settings, persisted_workspace):
        assert run_stage(uow, clock, settings, persisted_workspace.id).status is (
            RunStatus.SUCCEEDED
        )


class TestLedgerReporting:
    def test_stories_standing_only_on_aggregators_are_counted(
        self, uow, clock, settings, persisted_workspace, newsletter, journal
    ):
        """The number that decides whether the day's output is publishable."""
        story_with(uow, persisted_workspace, clock, newsletter, url="https://techcrunch.com/a")
        story_with(uow, persisted_workspace, clock, journal, url="https://arxiv.org/abs/1")

        ledger = run_stage(uow, clock, settings, persisted_workspace.id)

        with uow as active:
            record = next(
                r
                for r in active.runs.list_stages(persisted_workspace.id, ledger.id)
                if r.stage == "evidence"
            )
        assert record.meta["aggregator_only_stories"] == 1
        assert record.meta["by_kind"] == {"aggregator": 1, "primary": 1}

    def test_the_ledger_is_countable_by_kind(
        self, uow, clock, settings, persisted_workspace, newsletter, journal
    ):
        story_with(uow, persisted_workspace, clock, newsletter, url="https://techcrunch.com/a")
        story_with(uow, persisted_workspace, clock, journal, url="https://arxiv.org/abs/1")

        run_stage(uow, clock, settings, persisted_workspace.id)

        with uow as active:
            assert active.evidence.count_by_kind(persisted_workspace.id) == {
                "aggregator": 1,
                "primary": 1,
            }
            assert active.evidence.count_for_workspace(persisted_workspace.id) == 2

    def test_discarding_a_story_discards_its_evidence(
        self, uow, clock, settings, persisted_workspace, newsletter
    ):
        """Evidence supports a specific story and means nothing without it."""
        story_with(uow, persisted_workspace, clock, newsletter, url="https://x.test/a")
        run_stage(uow, clock, settings, persisted_workspace.id)

        with uow as active:
            active.stories.delete_all(persisted_workspace.id)
            active.commit()

        with uow as active:
            assert active.evidence.count_for_workspace(persisted_workspace.id) == 0
            assert active.candidates.count_for_workspace(persisted_workspace.id) == 1
