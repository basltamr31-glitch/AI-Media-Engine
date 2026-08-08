"""Tests for the normalisers and the signal computers.

This is the arithmetic the whole ranking rests on, so it is tested as
arithmetic: given these inputs, this number, and here is why that number is the
right one. A ranking that cannot be argued from a test case cannot be argued
with an editor either.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from engine.adapters.scoring.computers import (
    AudienceFitComputer,
    CorroborationComputer,
    ProminenceComputer,
    RecencyComputer,
    SourceAuthorityComputer,
)
from engine.core.domain.enums import SourceKind, StoryStatus
from engine.core.domain.pack import Normaliser
from engine.core.domain.policy import EditorialPolicy
from engine.core.domain.scoring import normalise
from engine.core.domain.source import Source
from engine.core.domain.story import Story, StoryCandidate
from engine.core.ports.scoring import SignalContext

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


class TestNormalisers:
    def test_passthrough_keeps_a_score_that_is_already_scaled(self):
        assert normalise(0.75, Normaliser.PASSTHROUGH) == 0.75

    @pytest.mark.parametrize("value", [1.7, -0.4])
    def test_passthrough_still_clamps(self, value: float):
        """A computer returning 1.7 would silently outvote its own weight."""
        assert 0.0 <= normalise(value, Normaliser.PASSTHROUGH) <= 1.0

    def test_linear_reaches_full_marks_at_the_ceiling(self):
        assert normalise(3, Normaliser.LINEAR, {"ceiling": 3}) == 1.0
        assert normalise(1, Normaliser.LINEAR, {"ceiling": 3}) == pytest.approx(1 / 3)

    def test_linear_does_not_exceed_full_marks_beyond_it(self):
        assert normalise(30, Normaliser.LINEAR, {"ceiling": 3}) == 1.0

    def test_logarithmic_rewards_the_first_units_most(self):
        """The same 90 extra upvotes are worth far more at 10 than at 1000."""
        early = normalise(100, Normaliser.LOGARITHMIC, {"ceiling": 10_000}) - normalise(
            10, Normaliser.LOGARITHMIC, {"ceiling": 10_000}
        )
        late = normalise(1090, Normaliser.LOGARITHMIC, {"ceiling": 10_000}) - normalise(
            1000, Normaliser.LOGARITHMIC, {"ceiling": 10_000}
        )
        assert early > late

    def test_logarithmic_of_nothing_is_zero(self):
        assert normalise(0, Normaliser.LOGARITHMIC, {"ceiling": 10}) == 0.0

    def test_decay_halves_at_the_half_life(self):
        assert normalise(24, Normaliser.DECAY, {"half_life_hours": 24}) == pytest.approx(0.5)
        assert normalise(48, Normaliser.DECAY, {"half_life_hours": 24}) == pytest.approx(0.25)

    def test_decay_of_something_brand_new_is_full_marks(self):
        assert normalise(0, Normaliser.DECAY, {"half_life_hours": 24}) == 1.0

    def test_a_negative_age_is_treated_as_new_not_as_ancient(self):
        """Clock skew must not promote a story above everything else."""
        assert normalise(-5, Normaliser.DECAY, {"half_life_hours": 24}) == 1.0

    @pytest.mark.parametrize("ceiling", [0, -3, None, "banana"])
    def test_an_unusable_ceiling_falls_back_instead_of_dividing_by_zero(self, ceiling: object):
        """Pack parameters are hand-written data."""
        assert 0.0 <= normalise(2, Normaliser.LINEAR, {"ceiling": ceiling}) <= 1.0

    def test_an_unusable_half_life_falls_back(self):
        assert 0.0 <= normalise(24, Normaliser.DECAY, {"half_life_hours": 0}) <= 1.0


def mention(
    *,
    source_id: str = "src_a",
    ordinal: int = 0,
    document_items: int = 1,
    points: int | None = None,
) -> StoryCandidate:
    meta: dict[str, object] = {"document_items": document_items}
    if points is not None:
        meta["points"] = points
    return StoryCandidate(
        workspace_id="ws",
        document_id="doc",
        source_id=source_id,
        headline="A story",
        ordinal=ordinal,
        extracted_at=NOW,
        meta=meta,
    )


def story(*, source_ids: list[str] | None = None, headline: str = "A story", age_hours: int = 0):
    return Story(
        workspace_id="ws",
        canonical_headline=headline,
        source_ids=source_ids or ["src_a"],
        status=StoryStatus.RESOLVED,
        first_seen_at=NOW - timedelta(hours=age_hours),
        last_seen_at=NOW,
    )


def context(
    mentions: list[StoryCandidate],
    *,
    sources: dict[str, Source] | None = None,
    policy: EditorialPolicy | None = None,
    parameters: dict[str, object] | None = None,
) -> SignalContext:
    return SignalContext(
        mentions=mentions,
        sources=sources or {},
        policy=policy or EditorialPolicy(),
        now=NOW,
        parameters=parameters or {},
    )


def source(source_id: str, trust: float) -> Source:
    return Source(
        id=source_id,
        workspace_id="ws",
        kind=SourceKind.RSS,
        name=source_id,
        identifier=f"https://{source_id}.test/feed",
        trust_weight=trust,
    )


class TestCorroboration:
    def test_it_counts_distinct_sources(self):
        computer = CorroborationComputer()
        assert computer.compute(story(source_ids=["a", "b", "c"]), context([])) == 3.0

    def test_one_source_repeating_itself_counts_once(self):
        """Otherwise a chatty newsletter could manufacture corroboration."""
        computer = CorroborationComputer()
        assert computer.compute(story(source_ids=["a", "a", "a"]), context([])) == 1.0


class TestSourceAuthority:
    def test_it_takes_the_most_trusted_source(self):
        """An aggregator also mentioning it does not make it less reliable."""
        computer = SourceAuthorityComputer()
        result = computer.compute(
            story(),
            context(
                [mention(source_id="low"), mention(source_id="high")],
                sources={"low": source("low", 0.4), "high": source("high", 0.9)},
            ),
        )
        assert result == 0.9

    def test_it_is_unmeasurable_without_known_sources(self):
        assert SourceAuthorityComputer().compute(story(), context([])) is None


class TestRecency:
    def test_it_reports_age_in_hours(self):
        assert RecencyComputer().compute(story(age_hours=12), context([])) == pytest.approx(12.0)

    def test_it_measures_from_first_seen_not_last(self):
        """A re-mentioned story is not thereby new again."""
        subject = story(age_hours=72)
        subject.last_seen_at = NOW
        assert RecencyComputer().compute(subject, context([])) == pytest.approx(72.0)

    def test_a_future_timestamp_is_clamped_to_zero(self):
        assert RecencyComputer().compute(story(age_hours=-10), context([])) == 0.0


class TestProminence:
    def test_a_newsletter_lead_item_scores_the_ceiling(self):
        result = ProminenceComputer().compute(
            story(),
            context([mention(ordinal=0, document_items=15)], parameters={"ceiling": 10}),
        )
        assert result == 10.0

    def test_a_later_item_scores_less(self):
        result = ProminenceComputer().compute(
            story(),
            context([mention(ordinal=4, document_items=15)], parameters={"ceiling": 10}),
        )
        assert result == 6.0

    def test_a_single_item_document_says_nothing_about_prominence(self):
        """Regression: every arXiv paper is position 0 because its feed item
        holds one paper, and scoring that as a lead item handed the whole
        source full marks for free."""
        result = ProminenceComputer().compute(
            story(),
            context([mention(ordinal=0, document_items=1)], parameters={"ceiling": 10}),
        )
        assert result is None

    def test_community_votes_count_where_there_is_no_ordering(self):
        result = ProminenceComputer().compute(
            story(),
            context([mention(ordinal=0, document_items=1, points=200)], parameters={"ceiling": 10}),
        )
        assert result == 10.0

    def test_position_and_votes_do_not_stack(self):
        """They are two expressions of one judgement, not two signals."""
        result = ProminenceComputer().compute(
            story(),
            context(
                [mention(ordinal=0, document_items=15, points=200)], parameters={"ceiling": 10}
            ),
        )
        assert result == 10.0

    def test_no_mentions_is_unmeasurable(self):
        assert ProminenceComputer().compute(story(), context([])) is None


class TestAudienceFit:
    def _policy(self, **kwargs: Any) -> EditorialPolicy:
        return EditorialPolicy(**kwargs)

    def test_a_story_squarely_on_a_priority_topic_scores_full(self):
        result = AudienceFitComputer().compute(
            story(headline="New AI agents ship from OpenAI"),
            context([], policy=self._policy(priority_topics=["AI agents"])),
        )
        assert result == pytest.approx(1.0)

    def test_an_unrelated_story_scores_zero(self):
        result = AudienceFitComputer().compute(
            story(headline="Football results roundup"),
            context([], policy=self._policy(priority_topics=["AI agents"])),
        )
        assert result == 0.0

    def test_a_blocked_topic_scores_zero_outright(self):
        result = AudienceFitComputer().compute(
            story(headline="Bitcoin price speculation continues"),
            context(
                [],
                policy=self._policy(
                    priority_topics=["AI agents"], blocked_topics=["price speculation"]
                ),
            ),
        )
        assert result == 0.0

    def test_matching_two_topics_loosely_beats_neither_but_not_one_exactly(self):
        policy = self._policy(priority_topics=["AI agents", "developer tooling"])
        exact = AudienceFitComputer().compute(
            story(headline="AI agents everywhere"), context([], policy=policy)
        )
        loose = AudienceFitComputer().compute(
            story(headline="Agents and tooling news"), context([], policy=policy)
        )
        assert exact is not None
        assert loose is not None
        assert exact == pytest.approx(1.0)
        assert loose < exact

    def test_it_is_unmeasurable_without_configured_topics(self):
        """No opinion configured is not the same as a bad match."""
        assert AudienceFitComputer().compute(story(), context([], policy=self._policy())) is None
