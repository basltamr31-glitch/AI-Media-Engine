"""Select stage -- decide what actually gets produced.

Scoring ranks everything; selection says no. That is the whole point:
``max_stories_per_run`` defaults to three because selectivity is the product
and volume is not, and a system that publishes the top of a list is not making
an editorial decision at all.

Three gates, each from the workspace's editorial policy and each refusing for a
different reason:

* **``minimum_score``** -- below this a story is never produced, even when
  nothing better exists. A thin news day should produce a thin day's output,
  not the best of a bad set dressed up as the day's news.
* **``require_primary_source``** -- a story nobody has read first-hand is
  second-hand, and publishing from it makes the engine a republisher. This is
  the gate that keeps "newsletters are tips, not text" true at the point where
  it would otherwise be quietly abandoned.
* **``max_stories_per_run``** -- the quota, counted over a window rather than
  per invocation, so running the command twice in one morning does not quietly
  produce a second batch.

Nothing is marked rejected. A story that missed today's cut may be exactly
right tomorrow -- corroboration arrives late, enrichment succeeds on a retry --
and a status that says "rejected" would make that unreachable. Not selected is
simply not selected.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from engine.core.domain.enums import StoryStatus
from engine.core.domain.policy import EditorialPolicy
from engine.core.domain.story import EditorialScore, Story
from engine.observability.logging import get_logger
from engine.pipeline.context import RunContext
from engine.pipeline.stages.base import StageResult

logger = get_logger(__name__)

#: Reasons a scored story was not selected. Counted in the ledger so a run that
#: produced nothing can say which gate did it -- "no stories today" is not an
#: acceptable answer on its own.
REASON_BELOW_MINIMUM: Final = "below_minimum_score"
REASON_NO_PRIMARY_SOURCE: Final = "no_primary_source"
REASON_NOT_SCORED: Final = "not_scored"
REASON_QUOTA_FULL: Final = "quota_reached"

#: Story meta keys written on selection.
RUN_KEY: Final = "selected_in_run"
RANK_KEY: Final = "selected_rank"
SCORE_KEY: Final = "selected_score"


class SelectStage:
    """Chooses the stories worth producing, and records why the rest were not."""

    def __init__(
        self,
        *,
        policy: EditorialPolicy,
        max_stories: int | None = None,
        window_hours: int = 24,
        candidate_window_days: int = 7,
    ) -> None:
        self._policy = policy
        # An override for the operator; the policy is the default, because the
        # quota is an editorial decision rather than an operational one.
        self._max_stories = max_stories or policy.max_stories_per_run
        self._window_hours = window_hours
        self._candidate_window_days = candidate_window_days

    @property
    def name(self) -> str:
        return "select"

    def run(self, context: RunContext) -> StageResult:
        now = context.clock.now()
        with context.uow as uow:
            recent = uow.stories.list_recent(
                context.workspace_id,
                limit=1000,
                since=now - timedelta(days=self._candidate_window_days),
            )
            if not recent:
                return StageResult.skip("no stories in the window")

            already = uow.stories.selected_since(
                context.workspace_id, since=now - timedelta(hours=self._window_hours)
            )
            scores = {
                story.id: uow.scores.latest_for_story(context.workspace_id, story.id)
                for story in recent
            }

        quota = max(0, self._max_stories - len(already))
        eligible, refused = self._sift(recent, scores)

        if quota == 0:
            # The day's quota is spent. Reported rather than silently doing
            # nothing, because "why did this run select nothing?" has exactly
            # two answers and they call for opposite responses.
            refused[REASON_QUOTA_FULL] = refused.get(REASON_QUOTA_FULL, 0) + len(eligible)
            return self._result(
                considered=len(recent),
                selected=[],
                refused=refused,
                already=len(already),
                context=context,
            )

        chosen = eligible[:quota]
        if not context.dry_run and chosen:
            with context.uow as uow:
                for rank, (story, score) in enumerate(chosen, start=len(already) + 1):
                    story.status = StoryStatus.SELECTED
                    story.selected_at = now
                    story.meta[RUN_KEY] = context.run_id
                    story.meta[RANK_KEY] = rank
                    story.meta[SCORE_KEY] = round(score.total, 6)
                    uow.stories.update(story)
                uow.commit()

        if len(eligible) > quota:
            refused[REASON_QUOTA_FULL] = refused.get(REASON_QUOTA_FULL, 0) + (len(eligible) - quota)

        return self._result(
            considered=len(recent),
            selected=chosen,
            refused=refused,
            already=len(already),
            context=context,
        )

    def _sift(
        self,
        stories: list[Story],
        scores: dict[str, EditorialScore | None],
    ) -> tuple[list[tuple[Story, EditorialScore]], dict[str, int]]:
        """Split the field into what may be produced and what may not.

        Gates are applied in order of how fundamental the refusal is, and each
        story is counted against the *first* reason it failed -- a story that is
        both unscored and unenriched is not two problems.
        """
        eligible: list[tuple[Story, EditorialScore]] = []
        refused: dict[str, int] = {}

        for story in stories:
            if story.status is StoryStatus.SELECTED:
                continue  # already chosen; counted separately

            score = scores.get(story.id)
            if score is None:
                refused[REASON_NOT_SCORED] = refused.get(REASON_NOT_SCORED, 0) + 1
                continue
            if score.total < self._policy.minimum_score:
                refused[REASON_BELOW_MINIMUM] = refused.get(REASON_BELOW_MINIMUM, 0) + 1
                continue
            if self._policy.require_primary_source and not story.is_publishable:
                refused[REASON_NO_PRIMARY_SOURCE] = refused.get(REASON_NO_PRIMARY_SOURCE, 0) + 1
                continue
            eligible.append((story, score))

        eligible.sort(key=lambda pair: pair[1].total, reverse=True)
        return eligible, refused

    def _result(
        self,
        *,
        considered: int,
        selected: list[tuple[Story, EditorialScore]],
        refused: dict[str, int],
        already: int,
        context: RunContext,
    ) -> StageResult:
        logger.info(
            "select.finished",
            considered=considered,
            selected=len(selected),
            already_selected=already,
            refused=refused,
        )
        return StageResult(
            items_in=considered,
            items_out=len(selected),
            meta={
                "considered": considered,
                "selected": len(selected),
                "already_selected_in_window": already,
                "refused": refused,
                "quota": self._max_stories,
                "window_hours": self._window_hours,
                "minimum_score": self._policy.minimum_score,
                "require_primary_source": self._policy.require_primary_source,
                "chosen": [
                    {
                        "story_id": story.id,
                        "headline": story.canonical_headline,
                        "score": round(score.total, 6),
                    }
                    for story, score in selected
                ],
                "dry_run": context.dry_run,
            },
        )
