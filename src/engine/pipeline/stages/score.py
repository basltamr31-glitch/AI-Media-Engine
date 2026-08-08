"""Score stage -- rank stories, showing the work.

Editorial judgement is the product. This stage is where it becomes a number,
and the reason every number is kept broken out rather than collapsed into a
total is that a ranking nobody can interrogate is a ranking nobody will trust
-- including us, when it puts the wrong story first.

Each :class:`~engine.core.domain.story.EditorialScore` records the weighted
contribution of every signal, so "why was this first?" is answered from stored
data. Those components are also the feature matrix that makes fitting weights
against real engagement possible in Phase 5; keeping only the total would throw
the training inputs away.

Scoring is pure arithmetic over rows already loaded: no network, no model, no
per-story cost. That is deliberate and load-bearing -- ranking runs over every
story on every run.
"""

from __future__ import annotations

from datetime import timedelta

from engine.adapters.scoring.registry import SignalRegistry
from engine.core.domain.pack import DomainPack, RankingSignal
from engine.core.domain.policy import EditorialPolicy, ScoringProfile
from engine.core.domain.scoring import normalise
from engine.core.domain.source import Source
from engine.core.domain.story import EditorialScore, Story, StoryCandidate
from engine.core.ports.scoring import SignalContext
from engine.observability.logging import get_logger
from engine.pipeline.context import RunContext
from engine.pipeline.stages.base import StageError, StageResult

logger = get_logger(__name__)


class ScoreStage:
    """Scores recent stories against the workspace's scoring profile."""

    def __init__(
        self,
        *,
        computers: SignalRegistry,
        pack: DomainPack,
        policy: EditorialPolicy,
        max_stories: int = 200,
        window_days: int = 7,
        rescore: bool = False,
    ) -> None:
        self._computers = computers
        self._pack = pack
        self._policy = policy
        self._max_stories = max_stories
        self._window_days = window_days
        # Scores are append-only history, so re-scoring is opt-in: a run that
        # silently added a second score for every story would make "what did we
        # think yesterday?" unanswerable.
        self._rescore = rescore

    @property
    def name(self) -> str:
        return "score"

    def run(self, context: RunContext) -> StageResult:
        profile = self._profile()
        if profile.is_empty:
            return StageResult.skip("no scoring profile: pack declares no ranking signals")

        signals = [
            signal for signal in self._pack.ranking_signals if signal.key in profile.signal_keys
        ]
        missing = self._computers.missing_for({signal.key for signal in signals})
        if missing and len(missing) == len(signals):
            msg = f"no computer is registered for any declared signal: {', '.join(missing)}"
            raise StageError(self.name, msg)

        since = context.clock.now() - timedelta(days=self._window_days)
        with context.uow as uow:
            stories = uow.stories.list_recent(
                context.workspace_id, limit=self._max_stories, since=since
            )
            if not stories:
                return StageResult.skip("no stories in the window")

            already_scored = (
                set()
                if self._rescore
                else uow.scores.stories_scored(
                    context.workspace_id, [story.id for story in stories]
                )
            )
            pending = [story for story in stories if story.id not in already_scored]
            if not pending:
                return StageResult.skip("every story in the window is already scored")

            sources = {
                source.id: source
                for source in uow.sources.list_for_workspace(
                    context.workspace_id, only_active=False
                )
            }
            mentions = {
                story.id: uow.candidates.list_for_story(context.workspace_id, story.id)
                for story in pending
            }

        scores = [
            self._score_one(
                story=story,
                mentions=mentions[story.id],
                sources=sources,
                signals=signals,
                profile=profile,
                context=context,
            )
            for story in pending
        ]

        if not context.dry_run and scores:
            with context.uow as uow:
                uow.scores.add_many(scores)
                uow.commit()

        best = max(scores, key=lambda score: score.total, default=None)
        logger.info(
            "score.finished",
            scored=len(scores),
            skipped=len(stories) - len(pending),
            missing_computers=missing,
            best=round(best.total, 4) if best else None,
        )
        return StageResult(
            items_in=len(stories),
            items_out=len(scores),
            meta={
                "scored": len(scores),
                "already_scored": len(stories) - len(pending),
                "signals": [signal.key for signal in signals],
                "missing_computers": missing,
                "best_total": round(best.total, 4) if best else 0.0,
                "above_minimum": sum(
                    1 for score in scores if score.total >= self._policy.minimum_score
                ),
                "minimum_score": self._policy.minimum_score,
                "rescore": self._rescore,
                "dry_run": context.dry_run,
            },
        )

    def _profile(self) -> ScoringProfile:
        """Workspace weights if set, the pack's defaults otherwise."""
        if not self._policy.scoring.is_empty:
            return self._policy.scoring
        return self._pack.default_scoring_profile

    def _score_one(
        self,
        *,
        story: Story,
        mentions: list[StoryCandidate],
        sources: dict[str, Source],
        signals: list[RankingSignal],
        profile: ScoringProfile,
        context: RunContext,
    ) -> EditorialScore:
        components: dict[str, float] = {}
        raw_values: dict[str, float] = {}
        unmeasured: list[str] = []

        for signal in signals:
            if signal.key not in self._computers:
                unmeasured.append(signal.key)
                continue
            signal_context = SignalContext(
                mentions=mentions,
                sources=sources,
                policy=self._policy,
                now=context.clock.now(),
                parameters=dict(signal.parameters),
            )
            raw = self._computers.get(signal.key).compute(story, signal_context)
            if raw is None:
                # Not measurable is not zero. Excluded from both the total and
                # the weight it would have carried, so an unmeasurable signal
                # cannot drag a story down for being unmeasurable.
                unmeasured.append(signal.key)
                continue
            raw_values[signal.key] = raw
            score = normalise(raw, signal.normaliser, signal.parameters)
            components[signal.key] = round(score * profile.weight_of(signal.key), 6)

        # Re-based over the signals that could actually be measured, so a story
        # missing one signal is compared on the same scale as one missing none.
        measured_weight = sum(profile.weight_of(key) for key in components)
        total = sum(components.values()) / measured_weight if measured_weight else 0.0

        return EditorialScore(
            workspace_id=story.workspace_id,
            story_id=story.id,
            run_id=context.run_id,
            total=round(min(1.0, max(0.0, total)), 6),
            components=components,
            rationale=_rationale(components, unmeasured),
            scored_at=context.clock.now(),
            raw=raw_values,
        )


def _rationale(components: dict[str, float], unmeasured: list[str]) -> str:
    """One line an editor can read without opening the database."""
    if not components:
        return "no signals could be measured"
    ranked = sorted(components.items(), key=lambda item: item[1], reverse=True)
    leading = ", ".join(f"{key} {value:.3f}" for key, value in ranked[:3])
    if unmeasured:
        return f"{leading} (not measurable: {', '.join(sorted(unmeasured))})"
    return leading
