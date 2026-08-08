"""Integration tests for the score stage.

What is protected here is the *explainability* contract as much as the numbers:
every component traceable to one signal, unmeasurable signals excluded rather
than counted as zero, and scores kept as history so a later run can ask what
the engine thought last week.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from engine.adapters.scoring.computers import (
    CorroborationComputer,
    ProminenceComputer,
    SourceAuthorityComputer,
)
from engine.adapters.scoring.registry import SignalRegistry
from engine.core.domain.enums import RunStatus, SourceKind, StoryStatus
from engine.core.domain.ledger import RunLedger
from engine.core.domain.pack import DomainPack, Normaliser, RankingSignal
from engine.core.domain.policy import EditorialPolicy, ScoringProfile
from engine.core.domain.source import Source, SourceDocument, compute_content_hash
from engine.core.domain.story import EditorialScore, Story, StoryCandidate
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.base import StageError
from engine.pipeline.stages.score import ScoreStage

pytestmark = pytest.mark.integration


def pack_with(*signals: RankingSignal) -> DomainPack:
    return DomainPack(key="test", version=1, name="Test", ranking_signals=list(signals))


def standard_pack() -> DomainPack:
    """Two signals summing to 1.0, both with computers behind them."""
    return pack_with(
        RankingSignal(
            key="corroboration",
            name="Corroboration",
            normaliser=Normaliser.LINEAR,
            default_weight=0.6,
            parameters={"ceiling": 3},
        ),
        RankingSignal(
            key="source_authority",
            name="Authority",
            normaliser=Normaliser.PASSTHROUGH,
            default_weight=0.4,
        ),
    )


def registry_with(*computers: Any) -> SignalRegistry:
    registry = SignalRegistry()
    for computer in computers:
        registry.register(computer)
    return registry


def standard_registry() -> SignalRegistry:
    return registry_with(CorroborationComputer(), SourceAuthorityComputer())


@pytest.fixture
def source_a(uow, persisted_workspace):
    return _source(uow, persisted_workspace, "Feed A", trust=0.8)


@pytest.fixture
def source_b(uow, persisted_workspace):
    return _source(uow, persisted_workspace, "Feed B", trust=0.4)


def _source(uow, workspace, name: str, *, trust: float) -> Source:
    source = Source(
        workspace_id=workspace.id,
        kind=SourceKind.RSS,
        name=name,
        identifier=f"https://{name.replace(' ', '').lower()}.test/feed",
        trust_weight=trust,
    )
    with uow as active:
        active.sources.add(source)
        active.commit()
    return source


def store_story(
    uow,
    workspace,
    clock,
    sources: list[Source],
    *,
    headline: str = "A story",
    ordinals: list[int] | None = None,
    items: int = 1,
) -> Story:
    """A story with one mention per source."""
    story = Story(
        workspace_id=workspace.id,
        canonical_headline=headline,
        status=StoryStatus.RESOLVED,
        first_seen_at=clock.now(),
        last_seen_at=clock.now(),
        source_ids=[source.id for source in sources],
    )
    with uow as active:
        active.stories.add(story)
        for index, source in enumerate(sources):
            document = SourceDocument(
                workspace_id=workspace.id,
                source_id=source.id,
                content_hash=compute_content_hash(headline, source.id),
                raw_content="body",
            )
            active.documents.add(document)
            active.candidates.add_many(
                [
                    StoryCandidate(
                        workspace_id=workspace.id,
                        document_id=document.id,
                        source_id=source.id,
                        story_id=story.id,
                        headline=headline,
                        ordinal=(ordinals or [0] * len(sources))[index],
                        extracted_at=clock.now(),
                        meta={"document_items": items},
                    )
                ]
            )
        active.commit()
    return story


def run_stage(
    uow, clock, settings, workspace_id: str, stage: ScoreStage, *, dry_run: bool = False
) -> RunLedger:
    runner = PipelineRunner(uow=uow, clock=clock, settings=settings)
    return runner.run(workspace_id=workspace_id, pipeline="score", stages=[stage], dry_run=dry_run)


def build_stage(
    *,
    pack: DomainPack | None = None,
    policy: EditorialPolicy | None = None,
    computers: SignalRegistry | None = None,
    **kwargs: Any,
) -> ScoreStage:
    # `is None`, not `or`: a registry defines __len__, so an *empty* one is
    # falsy and `computers or standard_registry()` would silently hand the
    # stage a full registry in exactly the test that wants an empty one.
    return ScoreStage(
        computers=standard_registry() if computers is None else computers,
        pack=standard_pack() if pack is None else pack,
        policy=EditorialPolicy() if policy is None else policy,
        **kwargs,
    )


def score_of(uow, workspace_id: str, story_id: str) -> EditorialScore:
    with uow as active:
        found: EditorialScore | None = active.scores.latest_for_story(workspace_id, story_id)
    assert found is not None
    return found


class TestScoring:
    def test_a_story_gets_a_score_with_its_components(
        self, uow, clock, settings, persisted_workspace, source_a
    ):
        story = store_story(uow, persisted_workspace, clock, [source_a])

        ledger = run_stage(uow, clock, settings, persisted_workspace.id, build_stage())

        assert ledger.status is RunStatus.SUCCEEDED
        score = score_of(uow, persisted_workspace.id, story.id)
        assert set(score.components) == {"corroboration", "source_authority"}
        assert score.total > 0

    def test_the_total_is_the_sum_of_the_components(
        self, uow, clock, settings, persisted_workspace, source_a
    ):
        """Nothing may enter the total that is not also shown."""
        story = store_story(uow, persisted_workspace, clock, [source_a])
        run_stage(uow, clock, settings, persisted_workspace.id, build_stage())

        score = score_of(uow, persisted_workspace.id, story.id)
        assert score.total == pytest.approx(sum(score.components.values()), abs=1e-6)

    def test_corroborated_stories_outrank_single_source_ones(
        self, uow, clock, settings, persisted_workspace, source_a, source_b
    ):
        """The whole editorial argument for reading more than one source."""
        alone = store_story(uow, persisted_workspace, clock, [source_a], headline="Alone")
        together = store_story(
            uow, persisted_workspace, clock, [source_a, source_b], headline="Together"
        )

        run_stage(uow, clock, settings, persisted_workspace.id, build_stage())

        assert (
            score_of(uow, persisted_workspace.id, together.id).total
            > score_of(uow, persisted_workspace.id, alone.id).total
        )

    def test_the_raw_measurements_are_kept(
        self, uow, clock, settings, persisted_workspace, source_a
    ):
        """Weights cannot be refitted later without the inputs (ADR 0016)."""
        story = store_story(uow, persisted_workspace, clock, [source_a])
        run_stage(uow, clock, settings, persisted_workspace.id, build_stage())

        score = score_of(uow, persisted_workspace.id, story.id)
        assert score.raw["corroboration"] == 1.0
        assert score.raw["source_authority"] == 0.8

    def test_the_rationale_names_the_leading_signals(
        self, uow, clock, settings, persisted_workspace, source_a
    ):
        story = store_story(uow, persisted_workspace, clock, [source_a])
        run_stage(uow, clock, settings, persisted_workspace.id, build_stage())

        assert "corroboration" in (score_of(uow, persisted_workspace.id, story.id).rationale or "")

    def test_a_dry_run_stores_nothing(self, uow, clock, settings, persisted_workspace, source_a):
        store_story(uow, persisted_workspace, clock, [source_a])
        run_stage(uow, clock, settings, persisted_workspace.id, build_stage(), dry_run=True)

        with uow as active:
            assert active.scores.count_for_workspace(persisted_workspace.id) == 0


class TestUnmeasurableSignals:
    def test_they_are_excluded_rather_than_counted_as_zero(
        self, uow, clock, settings, persisted_workspace, source_a
    ):
        """Regression: an unmeasurable signal must not drag a story down for
        being unmeasurable. Every arXiv paper scored full prominence marks
        because position 0 in a one-item document looked like a lead item."""
        pack = pack_with(
            RankingSignal(
                key="corroboration",
                name="Corroboration",
                normaliser=Normaliser.LINEAR,
                default_weight=0.5,
                parameters={"ceiling": 3},
            ),
            RankingSignal(
                key="prominence",
                name="Prominence",
                normaliser=Normaliser.LINEAR,
                default_weight=0.5,
                parameters={"ceiling": 10},
            ),
        )
        story = store_story(uow, persisted_workspace, clock, [source_a], items=1)

        run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            build_stage(
                pack=pack,
                computers=registry_with(CorroborationComputer(), ProminenceComputer()),
            ),
        )

        score = score_of(uow, persisted_workspace.id, story.id)
        assert "prominence" not in score.components
        assert "prominence" not in score.raw
        # Rebased over what could be measured, so the story is compared on the
        # same scale as one where everything was measurable.
        assert score.total == pytest.approx(1 / 3, abs=1e-6)

    def test_the_rationale_says_what_could_not_be_measured(
        self, uow, clock, settings, persisted_workspace, source_a
    ):
        pack = pack_with(
            RankingSignal(
                key="corroboration",
                name="Corroboration",
                normaliser=Normaliser.LINEAR,
                default_weight=0.5,
                parameters={"ceiling": 3},
            ),
            RankingSignal(
                key="prominence",
                name="Prominence",
                normaliser=Normaliser.LINEAR,
                default_weight=0.5,
            ),
        )
        story = store_story(uow, persisted_workspace, clock, [source_a], items=1)
        run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            build_stage(
                pack=pack,
                computers=registry_with(CorroborationComputer(), ProminenceComputer()),
            ),
        )

        assert "not measurable" in (score_of(uow, persisted_workspace.id, story.id).rationale or "")

    def test_a_signal_with_no_computer_is_reported_not_fatal(
        self, uow, clock, settings, persisted_workspace, source_a
    ):
        """A pack may declare a signal before its computer ships."""
        store_story(uow, persisted_workspace, clock, [source_a])

        ledger = run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            build_stage(computers=registry_with(CorroborationComputer())),
        )

        with uow as active:
            record = next(
                r
                for r in active.runs.list_stages(persisted_workspace.id, ledger.id)
                if r.stage == "score"
            )
        assert record.meta["missing_computers"] == ["source_authority"]

    def test_no_computers_at_all_fails_the_run(
        self, uow, clock, settings, persisted_workspace, source_a
    ):
        """Scoring everything at zero would look like a quiet news day."""
        store_story(uow, persisted_workspace, clock, [source_a])

        with pytest.raises(StageError, match="no computer is registered"):
            run_stage(
                uow,
                clock,
                settings,
                persisted_workspace.id,
                build_stage(computers=SignalRegistry()),
            )


class TestProfileResolution:
    def test_the_workspace_profile_wins_when_set(
        self, uow, clock, settings, persisted_workspace, source_a
    ):
        story = store_story(uow, persisted_workspace, clock, [source_a])
        policy = EditorialPolicy(
            scoring=ScoringProfile(weights={"corroboration": 0.1, "source_authority": 0.9})
        )

        run_stage(uow, clock, settings, persisted_workspace.id, build_stage(policy=policy))

        score = score_of(uow, persisted_workspace.id, story.id)
        assert score.components["source_authority"] > score.components["corroboration"]

    def test_the_pack_default_is_used_otherwise(
        self, uow, clock, settings, persisted_workspace, source_a
    ):
        """Weights come from the pack: 0.6 corroboration, 0.4 authority."""
        story = store_story(uow, persisted_workspace, clock, [source_a])
        run_stage(uow, clock, settings, persisted_workspace.id, build_stage())

        score = score_of(uow, persisted_workspace.id, story.id)
        # One source of three possible, weighted 0.6; trust 0.8 weighted 0.4.
        assert score.components["corroboration"] == pytest.approx(0.6 / 3, abs=1e-6)
        assert score.components["source_authority"] == pytest.approx(0.4 * 0.8, abs=1e-6)

    def test_a_pack_with_no_signals_is_a_skip(
        self, uow, clock, settings, persisted_workspace, source_a
    ):
        store_story(uow, persisted_workspace, clock, [source_a])

        ledger = run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            build_stage(pack=pack_with()),
        )
        assert ledger.status is RunStatus.SUCCEEDED


class TestHistory:
    def test_running_twice_does_not_rescore(
        self, uow, clock, settings, persisted_workspace, source_a
    ):
        store_story(uow, persisted_workspace, clock, [source_a])

        run_stage(uow, clock, settings, persisted_workspace.id, build_stage())
        run_stage(uow, clock, settings, persisted_workspace.id, build_stage())

        with uow as active:
            assert active.scores.count_for_workspace(persisted_workspace.id) == 1

    def test_rescoring_keeps_the_earlier_score(
        self, uow, clock, settings, persisted_workspace, source_a
    ):
        """ "What did we think last week, and with which weights?" is the
        question that makes refitting possible."""
        story = store_story(uow, persisted_workspace, clock, [source_a])
        run_stage(uow, clock, settings, persisted_workspace.id, build_stage())

        clock.advance(seconds=3600)
        run_stage(uow, clock, settings, persisted_workspace.id, build_stage(rescore=True))

        with uow as active:
            history = active.scores.history_for_story(persisted_workspace.id, story.id)
        assert len(history) == 2

    def test_the_latest_score_is_the_current_one(
        self, uow, clock, settings, persisted_workspace, source_a, source_b
    ):
        story = store_story(uow, persisted_workspace, clock, [source_a])
        run_stage(uow, clock, settings, persisted_workspace.id, build_stage())
        first = score_of(uow, persisted_workspace.id, story.id).total

        # A second source reports it, so corroboration rises.
        with uow as active:
            document = SourceDocument(
                workspace_id=persisted_workspace.id,
                source_id=source_b.id,
                content_hash=compute_content_hash("later", source_b.id),
                raw_content="body",
            )
            active.documents.add(document)
            active.candidates.add_many(
                [
                    StoryCandidate(
                        workspace_id=persisted_workspace.id,
                        document_id=document.id,
                        source_id=source_b.id,
                        story_id=story.id,
                        headline="A story",
                        extracted_at=clock.now(),
                    )
                ]
            )
            stored = active.stories.get(persisted_workspace.id, story.id)
            assert stored is not None
            active.commit()

        clock.advance(seconds=60)
        run_stage(uow, clock, settings, persisted_workspace.id, build_stage(rescore=True))

        assert score_of(uow, persisted_workspace.id, story.id).total > first

    def test_stories_outside_the_window_are_not_scored(
        self, uow, clock, settings, persisted_workspace, source_a
    ):
        store_story(uow, persisted_workspace, clock, [source_a])
        clock.advance(seconds=int(timedelta(days=30).total_seconds()))

        ledger = run_stage(uow, clock, settings, persisted_workspace.id, build_stage(window_days=7))

        assert ledger.status is RunStatus.SUCCEEDED
        with uow as active:
            assert active.scores.count_for_workspace(persisted_workspace.id) == 0
