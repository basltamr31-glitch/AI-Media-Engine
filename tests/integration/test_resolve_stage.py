"""Integration tests for the resolve stage, against a real database.

The behaviour worth protecting is what the stage refuses to do: merge two
stories that merely sound alike, or split one story reported by two sources.
Both failures are silent and both are expensive, so they are tested directly
rather than inferred from counts.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from engine.core.domain.enums import RunStatus, SourceKind, StoryStatus
from engine.core.domain.ledger import RunLedger
from engine.core.domain.source import Source, SourceDocument, compute_content_hash
from engine.core.domain.story import Story, StoryCandidate
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.resolve import ResolveStage

pytestmark = pytest.mark.integration


@pytest.fixture
def newsletter(uow, persisted_workspace):
    return _source(uow, persisted_workspace, "TLDR AI", SourceKind.EMAIL, trust=0.60)


@pytest.fixture
def community(uow, persisted_workspace):
    return _source(uow, persisted_workspace, "Hacker News", SourceKind.API, trust=0.45)


def _source(uow, workspace, name, kind, *, trust: float) -> Source:
    source = Source(
        workspace_id=workspace.id,
        kind=kind,
        name=name,
        identifier=f"https://{name.replace(' ', '').lower()}.test",
        trust_weight=trust,
    )
    with uow as active:
        active.sources.add(source)
        active.commit()
    return source


def mention(
    uow,
    source: Source,
    clock,
    *,
    headline: str,
    url: str | None = None,
    summary: str | None = None,
    offset_hours: int = 0,
) -> StoryCandidate:
    """Store one document and one candidate extracted from it."""
    document = SourceDocument(
        workspace_id=source.workspace_id,
        source_id=source.id,
        content_hash=compute_content_hash(headline, source.id, str(offset_hours)),
        title=headline,
        raw_content="body",
    )
    candidate = StoryCandidate(
        workspace_id=source.workspace_id,
        document_id=document.id,
        source_id=source.id,
        headline=headline,
        summary=summary,
        primary_url=url,
        extracted_at=clock.now() + timedelta(hours=offset_hours),
    )
    with uow as active:
        active.documents.add(document)
        active.candidates.add_many([candidate])
        active.commit()
    return candidate


def run_stage(
    uow,
    clock,
    settings,
    workspace_id: str,
    stage: ResolveStage | None = None,
    *,
    dry_run: bool = False,
) -> RunLedger:
    runner = PipelineRunner(uow=uow, clock=clock, settings=settings)
    return runner.run(
        workspace_id=workspace_id,
        pipeline="resolve",
        stages=[stage or ResolveStage()],
        dry_run=dry_run,
    )


def stories_of(uow, workspace_id: str) -> list[Story]:
    with uow as active:
        stories: list[Story] = active.stories.list_recent(workspace_id, limit=100)
    return stories


class TestClusteringByLink:
    def test_two_sources_linking_the_same_article_make_one_story(
        self, uow, clock, settings, persisted_workspace, newsletter, community
    ):
        """The whole reason for reading more than one source."""
        mention(
            uow,
            newsletter,
            clock,
            headline="Google reshuffles AI leadership",
            url="https://blog.google/next-chapter",
        )
        mention(
            uow,
            community,
            clock,
            headline="Changes at Google DeepMind",
            url="https://blog.google/next-chapter",
        )

        run_stage(uow, clock, settings, persisted_workspace.id)

        stories = stories_of(uow, persisted_workspace.id)
        assert len(stories) == 1
        assert stories[0].source_count == 2
        assert len(stories[0].candidate_ids) == 2

    def test_campaign_tags_do_not_split_a_story(
        self, uow, clock, settings, persisted_workspace, newsletter, community
    ):
        mention(uow, newsletter, clock, headline="A launch", url="https://x.test/a?utm_source=tldr")
        mention(uow, community, clock, headline="A launch, covered", url="https://www.x.test/a/")

        run_stage(uow, clock, settings, persisted_workspace.id)
        assert len(stories_of(uow, persisted_workspace.id)) == 1

    def test_different_articles_stay_apart(
        self, uow, clock, settings, persisted_workspace, newsletter
    ):
        mention(uow, newsletter, clock, headline="First", url="https://x.test/a")
        mention(uow, newsletter, clock, headline="Second", url="https://x.test/b")

        run_stage(uow, clock, settings, persisted_workspace.id)
        assert len(stories_of(uow, persisted_workspace.id)) == 2

    def test_two_mentions_with_no_links_do_not_match_on_nothing(
        self, uow, clock, settings, persisted_workspace, newsletter
    ):
        """A missing key means unknown, never "equal to the other unknown"."""
        mention(uow, newsletter, clock, headline="Something happened")
        mention(uow, newsletter, clock, headline="Something else happened")

        run_stage(uow, clock, settings, persisted_workspace.id)
        assert len(stories_of(uow, persisted_workspace.id)) == 2


class TestClusteringByHeadline:
    def test_identical_wording_merges_without_a_link(
        self, uow, clock, settings, persisted_workspace, newsletter, community
    ):
        mention(uow, newsletter, clock, headline="The AI Aesthetic")
        mention(uow, community, clock, headline="the ai aesthetic")

        run_stage(uow, clock, settings, persisted_workspace.id)
        assert len(stories_of(uow, persisted_workspace.id)) == 1

    def test_similar_but_different_papers_are_not_merged_by_default(
        self, uow, clock, settings, persisted_workspace, newsletter
    ):
        """Measured at 0.67 on the real corpus. Merging them would be wrong."""
        mention(
            uow,
            newsletter,
            clock,
            headline="On the missing benchmarks layer and a potential solution",
            url="https://arxiv.test/1",
        )
        mention(
            uow,
            newsletter,
            clock,
            headline="On the missing data layer and a potential solution",
            url="https://arxiv.test/2",
        )

        run_stage(uow, clock, settings, persisted_workspace.id)
        assert len(stories_of(uow, persisted_workspace.id)) == 2

    def test_lowering_the_threshold_merges_them(
        self, uow, clock, settings, persisted_workspace, newsletter
    ):
        """The knob exists; the default is where it is on purpose."""
        mention(
            uow,
            newsletter,
            clock,
            headline="On the missing benchmarks layer and a potential solution",
            url="https://arxiv.test/1",
        )
        mention(
            uow,
            newsletter,
            clock,
            headline="On the missing data layer and a potential solution",
            url="https://arxiv.test/2",
        )

        run_stage(
            uow, clock, settings, persisted_workspace.id, ResolveStage(similarity_threshold=0.6)
        )
        assert len(stories_of(uow, persisted_workspace.id)) == 1


class TestCanonicalFields:
    def test_the_most_trusted_source_supplies_the_headline(
        self, uow, clock, settings, persisted_workspace, newsletter, community
    ):
        """A wire service's wording should beat an aggregator's rewrite."""
        mention(uow, community, clock, headline="Community rewrite", url="https://x.test/a")
        mention(
            uow,
            newsletter,
            clock,
            headline="Publisher wording",
            url="https://x.test/a",
            offset_hours=1,
        )

        run_stage(uow, clock, settings, persisted_workspace.id)
        story = stories_of(uow, persisted_workspace.id)[0]
        assert story.canonical_headline == "Publisher wording"

    def test_a_less_trusted_later_mention_does_not_overwrite_it(
        self, uow, clock, settings, persisted_workspace, newsletter, community
    ):
        """Regression: the founding mention's trust has to be recorded."""
        mention(uow, newsletter, clock, headline="Publisher wording", url="https://x.test/a")
        mention(
            uow,
            community,
            clock,
            headline="Community rewrite",
            url="https://x.test/a",
            offset_hours=1,
        )

        run_stage(uow, clock, settings, persisted_workspace.id)
        story = stories_of(uow, persisted_workspace.id)[0]
        assert story.canonical_headline == "Publisher wording"

    def test_a_story_gains_a_link_from_whichever_source_has_one(
        self, uow, clock, settings, persisted_workspace, newsletter, community
    ):
        """That link is what enrichment follows."""
        mention(uow, newsletter, clock, headline="Same headline here")
        mention(
            uow,
            community,
            clock,
            headline="Same headline here",
            url="https://x.test/a",
            offset_hours=1,
        )

        run_stage(uow, clock, settings, persisted_workspace.id)
        assert stories_of(uow, persisted_workspace.id)[0].primary_url == "https://x.test/a"

    def test_first_and_last_seen_span_every_mention(
        self, uow, clock, settings, persisted_workspace, newsletter, community
    ):
        mention(uow, newsletter, clock, headline="A story", url="https://x.test/a")
        mention(
            uow, community, clock, headline="A story again", url="https://x.test/a", offset_hours=6
        )

        run_stage(uow, clock, settings, persisted_workspace.id)
        story = stories_of(uow, persisted_workspace.id)[0]
        assert (story.last_seen_at - story.first_seen_at) == timedelta(hours=6)

    def test_a_resolved_story_carries_the_resolved_status(
        self, uow, clock, settings, persisted_workspace, newsletter
    ):
        mention(uow, newsletter, clock, headline="A story", url="https://x.test/a")
        run_stage(uow, clock, settings, persisted_workspace.id)
        assert stories_of(uow, persisted_workspace.id)[0].status is StoryStatus.RESOLVED


class TestIdempotencyAndWindows:
    def test_running_twice_creates_nothing_new(
        self, uow, clock, settings, persisted_workspace, newsletter
    ):
        mention(uow, newsletter, clock, headline="A story", url="https://x.test/a")

        run_stage(uow, clock, settings, persisted_workspace.id)
        ledger = run_stage(uow, clock, settings, persisted_workspace.id)

        assert ledger.status is RunStatus.SUCCEEDED
        assert len(stories_of(uow, persisted_workspace.id)) == 1

    def test_a_later_mention_joins_an_existing_story(
        self, uow, clock, settings, persisted_workspace, newsletter, community
    ):
        """A story covered on Monday and again on Thursday is one story."""
        mention(uow, newsletter, clock, headline="Monday coverage", url="https://x.test/a")
        run_stage(uow, clock, settings, persisted_workspace.id)

        mention(
            uow,
            community,
            clock,
            headline="Thursday coverage",
            url="https://x.test/a",
            offset_hours=72,
        )
        run_stage(uow, clock, settings, persisted_workspace.id)

        stories = stories_of(uow, persisted_workspace.id)
        assert len(stories) == 1
        assert stories[0].source_count == 2

    def test_a_mention_outside_the_window_starts_a_new_story(
        self, uow, clock, settings, persisted_workspace, newsletter
    ):
        """Old news resurfacing is new news, not an update to a stale story.

        The window is measured against wall-clock now, so the clock has to move
        for this to be a real test rather than a statement about timestamps.
        """
        mention(uow, newsletter, clock, headline="Original", url="https://x.test/a")
        run_stage(uow, clock, settings, persisted_workspace.id, ResolveStage(window_days=1))

        clock.advance(seconds=10 * 24 * 3600)
        mention(uow, newsletter, clock, headline="Resurfaced", url="https://x.test/a")
        run_stage(uow, clock, settings, persisted_workspace.id, ResolveStage(window_days=1))

        assert len(stories_of(uow, persisted_workspace.id)) == 2

    def test_a_dry_run_stores_nothing(self, uow, clock, settings, persisted_workspace, newsletter):
        mention(uow, newsletter, clock, headline="A story", url="https://x.test/a")

        run_stage(uow, clock, settings, persisted_workspace.id, dry_run=True)

        assert stories_of(uow, persisted_workspace.id) == []
        with uow as active:
            assert len(active.candidates.list_unresolved(persisted_workspace.id)) == 1

    def test_nothing_to_resolve_is_a_skip_not_a_failure(
        self, uow, clock, settings, persisted_workspace, newsletter
    ):
        ledger = run_stage(uow, clock, settings, persisted_workspace.id)
        assert ledger.status is RunStatus.SUCCEEDED


class TestLedger:
    def test_the_ledger_records_how_each_match_was_made(
        self, uow, clock, settings, persisted_workspace, newsletter, community
    ):
        """ "Why is this one story?" has to be answerable from data."""
        mention(uow, newsletter, clock, headline="A story", url="https://x.test/a")
        mention(uow, community, clock, headline="Rewritten", url="https://x.test/a")
        mention(uow, newsletter, clock, headline="Unrelated", url="https://x.test/b")

        ledger = run_stage(uow, clock, settings, persisted_workspace.id)

        with uow as active:
            record = next(
                r
                for r in active.runs.list_stages(persisted_workspace.id, ledger.id)
                if r.stage == "resolve"
            )
        assert record.meta["matched_by"] == {"url": 1, "title": 0, "similarity": 0, "new": 2}
        assert record.meta["stories_created"] == 2
        assert record.meta["multi_mention_stories"] == 1


class TestReclustering:
    def test_discarding_stories_returns_mentions_to_the_pool(
        self, uow, clock, settings, persisted_workspace, newsletter
    ):
        """Threshold tuning is impossible otherwise."""
        mention(uow, newsletter, clock, headline="A story", url="https://x.test/a")
        run_stage(uow, clock, settings, persisted_workspace.id)

        with uow as active:
            assert active.stories.delete_all(persisted_workspace.id) == 1
            active.commit()

        with uow as active:
            assert active.stories.count_for_workspace(persisted_workspace.id) == 0
            assert len(active.candidates.list_unresolved(persisted_workspace.id)) == 1

    def test_the_mentions_themselves_survive(
        self, uow, clock, settings, persisted_workspace, newsletter
    ):
        mention(uow, newsletter, clock, headline="A story", url="https://x.test/a")
        run_stage(uow, clock, settings, persisted_workspace.id)

        with uow as active:
            active.stories.delete_all(persisted_workspace.id)
            active.commit()
        with uow as active:
            assert active.candidates.count_for_workspace(persisted_workspace.id) == 1
