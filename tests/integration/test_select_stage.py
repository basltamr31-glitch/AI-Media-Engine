"""Integration tests for the select stage.

Selection is the stage that says no, so the tests are mostly about refusals:
each gate refusing for its own reason, the quota holding across repeated runs,
and nothing being marked rejected — a story that missed today's cut has to
remain selectable tomorrow.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from engine.core.domain.enums import RunStatus, SourceKind, StoryStatus
from engine.core.domain.ledger import RunLedger
from engine.core.domain.policy import EditorialPolicy
from engine.core.domain.source import Source
from engine.core.domain.story import EditorialScore, EnrichedSource, Story
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.select import RANK_KEY, SCORE_KEY, SelectStage

pytestmark = pytest.mark.integration


@pytest.fixture
def feed(uow, persisted_workspace) -> Source:
    source = Source(
        workspace_id=persisted_workspace.id,
        kind=SourceKind.RSS,
        name="Feed",
        identifier="https://feed.test/rss",
    )
    with uow as active:
        active.sources.add(source)
        active.commit()
    return source


def store_story(
    uow,
    workspace,
    clock,
    *,
    headline: str = "A story",
    score: float | None = 0.8,
    enriched: bool = True,
) -> Story:
    """A story in whatever state the gate under test needs."""
    story = Story(
        workspace_id=workspace.id,
        canonical_headline=headline,
        primary_url=f"https://example.test/{headline.replace(' ', '-').lower()}",
        status=StoryStatus.RESOLVED,
        first_seen_at=clock.now(),
        last_seen_at=clock.now(),
    )
    if enriched:
        story.enrichment = EnrichedSource(
            url=story.primary_url or "", text="The announcement in full.", fetched_at=clock.now()
        )
        story.status = StoryStatus.ENRICHED

    with uow as active:
        active.stories.add(story)
        if score is not None:
            active.scores.add_many(
                [
                    EditorialScore(
                        workspace_id=workspace.id,
                        story_id=story.id,
                        run_id="run_seed",
                        total=score,
                        components={"corroboration": score},
                        scored_at=clock.now(),
                    )
                ]
            )
        active.commit()
    return story


def run_stage(
    uow, clock, settings, workspace_id: str, stage: SelectStage, *, dry_run: bool = False
) -> RunLedger:
    runner = PipelineRunner(uow=uow, clock=clock, settings=settings)
    return runner.run(workspace_id=workspace_id, pipeline="select", stages=[stage], dry_run=dry_run)


def stage_meta(uow, workspace_id: str, ledger: RunLedger) -> dict[str, Any]:
    with uow as active:
        record = next(
            r for r in active.runs.list_stages(workspace_id, ledger.id) if r.stage == "select"
        )
    meta: dict[str, Any] = record.meta
    return meta


def selected(uow, workspace_id: str) -> list[Story]:
    with uow as active:
        stories: list[Story] = active.stories.list_selected(workspace_id, limit=50)
    return stories


class TestSelection:
    def test_the_best_scoring_stories_are_chosen(
        self, uow, clock, settings, persisted_workspace, feed
    ):
        store_story(uow, persisted_workspace, clock, headline="Weak", score=0.4)
        store_story(uow, persisted_workspace, clock, headline="Strong", score=0.9)

        ledger = run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            SelectStage(policy=EditorialPolicy(max_stories_per_run=1)),
        )

        assert ledger.status is RunStatus.SUCCEEDED
        chosen = selected(uow, persisted_workspace.id)
        assert [story.canonical_headline for story in chosen] == ["Strong"]

    def test_a_selected_story_records_its_rank_and_score(
        self, uow, clock, settings, persisted_workspace, feed
    ):
        store_story(uow, persisted_workspace, clock, score=0.9)

        run_stage(
            uow, clock, settings, persisted_workspace.id, SelectStage(policy=EditorialPolicy())
        )

        story = selected(uow, persisted_workspace.id)[0]
        assert story.status is StoryStatus.SELECTED
        assert story.is_selected
        assert story.meta[RANK_KEY] == 1
        assert story.meta[SCORE_KEY] == pytest.approx(0.9)

    def test_a_dry_run_selects_nothing(self, uow, clock, settings, persisted_workspace, feed):
        store_story(uow, persisted_workspace, clock, score=0.9)

        run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            SelectStage(policy=EditorialPolicy()),
            dry_run=True,
        )

        assert selected(uow, persisted_workspace.id) == []

    def test_the_ledger_lists_what_was_chosen(
        self, uow, clock, settings, persisted_workspace, feed
    ):
        store_story(uow, persisted_workspace, clock, headline="Chosen", score=0.9)

        ledger = run_stage(
            uow, clock, settings, persisted_workspace.id, SelectStage(policy=EditorialPolicy())
        )

        meta = stage_meta(uow, persisted_workspace.id, ledger)
        assert [entry["headline"] for entry in meta["chosen"]] == ["Chosen"]


class TestGates:
    def test_a_story_below_the_minimum_is_never_produced(
        self, uow, clock, settings, persisted_workspace, feed
    ):
        """A thin news day should produce a thin day, not the best of a bad set."""
        store_story(uow, persisted_workspace, clock, score=0.2)

        ledger = run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            SelectStage(policy=EditorialPolicy(minimum_score=0.35)),
        )

        assert selected(uow, persisted_workspace.id) == []
        assert stage_meta(uow, persisted_workspace.id, ledger)["refused"] == {
            "below_minimum_score": 1
        }

    def test_a_story_with_no_primary_source_is_refused(
        self, uow, clock, settings, persisted_workspace, feed
    ):
        """The gate that keeps "newsletters are tips, not text" true."""
        store_story(uow, persisted_workspace, clock, score=0.9, enriched=False)

        ledger = run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            SelectStage(policy=EditorialPolicy(require_primary_source=True)),
        )

        assert selected(uow, persisted_workspace.id) == []
        assert stage_meta(uow, persisted_workspace.id, ledger)["refused"] == {
            "no_primary_source": 1
        }

    def test_the_primary_source_gate_can_be_turned_off(
        self, uow, clock, settings, persisted_workspace, feed
    ):
        """Turning it off makes the engine a republisher -- but it is a choice."""
        store_story(uow, persisted_workspace, clock, score=0.9, enriched=False)

        run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            SelectStage(policy=EditorialPolicy(require_primary_source=False)),
        )

        assert len(selected(uow, persisted_workspace.id)) == 1

    def test_an_unscored_story_is_refused_and_named_as_such(
        self, uow, clock, settings, persisted_workspace, feed
    ):
        store_story(uow, persisted_workspace, clock, score=None)

        ledger = run_stage(
            uow, clock, settings, persisted_workspace.id, SelectStage(policy=EditorialPolicy())
        )

        assert stage_meta(uow, persisted_workspace.id, ledger)["refused"] == {"not_scored": 1}

    def test_each_story_is_counted_against_one_reason_only(
        self, uow, clock, settings, persisted_workspace, feed
    ):
        """Unscored *and* unenriched is one problem, not two."""
        store_story(uow, persisted_workspace, clock, score=None, enriched=False)

        ledger = run_stage(
            uow, clock, settings, persisted_workspace.id, SelectStage(policy=EditorialPolicy())
        )

        refused = stage_meta(uow, persisted_workspace.id, ledger)["refused"]
        assert sum(refused.values()) == 1


class TestQuota:
    def test_the_policy_quota_is_respected(self, uow, clock, settings, persisted_workspace, feed):
        """Selectivity is the product; volume is not."""
        for index in range(6):
            store_story(uow, persisted_workspace, clock, headline=f"Story {index}", score=0.9)

        run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            SelectStage(policy=EditorialPolicy(max_stories_per_run=3)),
        )

        assert len(selected(uow, persisted_workspace.id)) == 3

    def test_running_twice_in_the_window_does_not_produce_a_second_batch(
        self, uow, clock, settings, persisted_workspace, feed
    ):
        """The quota is counted over a window, not per invocation."""
        for index in range(6):
            store_story(uow, persisted_workspace, clock, headline=f"Story {index}", score=0.9)
        stage = SelectStage(policy=EditorialPolicy(max_stories_per_run=3), window_hours=24)

        run_stage(uow, clock, settings, persisted_workspace.id, stage)
        ledger = run_stage(uow, clock, settings, persisted_workspace.id, stage)

        assert len(selected(uow, persisted_workspace.id)) == 3
        meta = stage_meta(uow, persisted_workspace.id, ledger)
        assert meta["already_selected_in_window"] == 3
        assert meta["refused"]["quota_reached"] >= 1

    def test_a_partly_filled_quota_is_topped_up(
        self, uow, clock, settings, persisted_workspace, feed
    ):
        store_story(uow, persisted_workspace, clock, headline="First", score=0.9)
        stage = SelectStage(policy=EditorialPolicy(max_stories_per_run=3))
        run_stage(uow, clock, settings, persisted_workspace.id, stage)

        store_story(uow, persisted_workspace, clock, headline="Second", score=0.8)
        run_stage(uow, clock, settings, persisted_workspace.id, stage)

        assert len(selected(uow, persisted_workspace.id)) == 2

    def test_the_quota_frees_up_after_the_window(
        self, uow, clock, settings, persisted_workspace, feed
    ):
        """Tomorrow is a new day's publishing, not a continuation of today's."""
        for index in range(4):
            store_story(uow, persisted_workspace, clock, headline=f"Story {index}", score=0.9)
        stage = SelectStage(policy=EditorialPolicy(max_stories_per_run=2), window_hours=24)

        run_stage(uow, clock, settings, persisted_workspace.id, stage)
        clock.advance(seconds=int(timedelta(hours=25).total_seconds()))
        run_stage(uow, clock, settings, persisted_workspace.id, stage)

        assert len(selected(uow, persisted_workspace.id)) == 4

    def test_an_operator_can_override_the_quota(
        self, uow, clock, settings, persisted_workspace, feed
    ):
        for index in range(5):
            store_story(uow, persisted_workspace, clock, headline=f"Story {index}", score=0.9)

        run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            SelectStage(policy=EditorialPolicy(max_stories_per_run=2), max_stories=5),
        )

        assert len(selected(uow, persisted_workspace.id)) == 5

    def test_stories_beaten_by_the_quota_are_counted_separately(
        self, uow, clock, settings, persisted_workspace, feed
    ):
        """ "Good enough but we only publish three" is not the same refusal as
        "not good enough"."""
        for index in range(5):
            store_story(uow, persisted_workspace, clock, headline=f"Story {index}", score=0.9)

        ledger = run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            SelectStage(policy=EditorialPolicy(max_stories_per_run=2)),
        )

        assert stage_meta(uow, persisted_workspace.id, ledger)["refused"]["quota_reached"] == 3


class TestNoRejection:
    def test_a_story_that_missed_the_cut_stays_selectable(
        self, uow, clock, settings, persisted_workspace, feed
    ):
        """Corroboration arrives late; enrichment succeeds on a retry. A
        "rejected" status would make tomorrow unreachable."""
        first = store_story(uow, persisted_workspace, clock, headline="First", score=0.9)
        second = store_story(uow, persisted_workspace, clock, headline="Second", score=0.5)
        stage = SelectStage(policy=EditorialPolicy(max_stories_per_run=1), window_hours=1)

        run_stage(uow, clock, settings, persisted_workspace.id, stage)
        clock.advance(seconds=7200)
        run_stage(uow, clock, settings, persisted_workspace.id, stage)

        chosen = {story.id for story in selected(uow, persisted_workspace.id)}
        assert chosen == {first.id, second.id}

    def test_nothing_is_ever_marked_rejected(self, uow, clock, settings, persisted_workspace, feed):
        store_story(uow, persisted_workspace, clock, score=0.1)

        run_stage(
            uow, clock, settings, persisted_workspace.id, SelectStage(policy=EditorialPolicy())
        )

        with uow as active:
            statuses = active.stories.count_by_status(persisted_workspace.id)
        assert StoryStatus.REJECTED.value not in statuses


class TestNothingToDo:
    def test_an_empty_workspace_is_a_skip(self, uow, clock, settings, persisted_workspace):
        ledger = run_stage(
            uow, clock, settings, persisted_workspace.id, SelectStage(policy=EditorialPolicy())
        )
        assert ledger.status is RunStatus.SUCCEEDED

    def test_stories_outside_the_candidate_window_are_not_considered(
        self, uow, clock, settings, persisted_workspace, feed
    ):
        store_story(uow, persisted_workspace, clock, score=0.9)
        clock.advance(seconds=int(timedelta(days=30).total_seconds()))

        run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            SelectStage(policy=EditorialPolicy(), candidate_window_days=7),
        )

        assert selected(uow, persisted_workspace.id) == []
