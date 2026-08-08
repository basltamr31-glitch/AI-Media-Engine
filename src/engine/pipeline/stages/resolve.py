"""Resolve stage -- collapse mentions into canonical stories.

Five newsletters covering one launch is one story, not five. Getting that wrong
in either direction is expensive: split a story and the day's output repeats
itself; fuse two and the result is a post about something that did not happen,
which cannot be undone downstream because the mentions are already merged.

So the matching is deliberately asymmetric. **Where a link points is strong
evidence; how a headline is worded is weak evidence.** Measured against the
real corpus (546 candidates from three sources), URL identity clustered 19
groups correctly and produced no false merges, while headline overlap put a
false positive (two different arXiv papers, 0.67) *above* the genuine
cross-source pairs (0.40-0.44). Fuzzy matching therefore ships switched off in
practice -- the default threshold sits above anything observed -- and stays
tunable for operators who want to trade precision for recall.

Clustering is a pure function of stored rows: no LLM call, no network, and
nothing that scales in cost with the number of stories. Semantic matching is a
Phase 3+ question, recorded in FUTURE_IDEAS rather than guessed at here.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from engine.core.domain.enums import StoryStatus
from engine.core.domain.matching import similarity, title_key, url_key
from engine.core.domain.source import Source
from engine.core.domain.story import Story, StoryCandidate
from engine.observability.logging import get_logger
from engine.pipeline.context import RunContext
from engine.pipeline.stages.base import StageError, StageResult

logger = get_logger(__name__)

#: A threshold above every headline pair observed in the calibration corpus.
#: Effectively "exact wording only", which is the honest default given that
#: measurement. Lower it deliberately, and check what merges.
DEFAULT_SIMILARITY_THRESHOLD = 0.9


class ResolveStage:
    """Clusters unresolved candidates into canonical stories."""

    def __init__(
        self,
        *,
        max_candidates: int = 500,
        window_days: int = 7,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> None:
        self._max_candidates = max_candidates
        # Today's mention of a story first seen on Monday must join Monday's
        # story, so matching looks back rather than at this run's work alone.
        self._window_days = window_days
        self._similarity_threshold = similarity_threshold

    @property
    def name(self) -> str:
        return "resolve"

    def run(self, context: RunContext) -> StageResult:
        with context.uow as uow:
            candidates = uow.candidates.list_unresolved(
                context.workspace_id, limit=self._max_candidates
            )
            if not candidates:
                return StageResult.skip("no unresolved candidates")

            since = context.clock.now() - timedelta(days=self._window_days)
            existing = uow.stories.list_recent(context.workspace_id, limit=1000, since=since)
            trust = {
                source.id: source
                for source in uow.sources.list_for_workspace(
                    context.workspace_id, only_active=False
                )
            }

        working = _WorkingSet(existing, threshold=self._similarity_threshold)
        created: list[Story] = []
        updated: dict[str, Story] = {}
        assignments: dict[str, list[str]] = {}
        matched_by: dict[str, int] = {"url": 0, "title": 0, "similarity": 0, "new": 0}

        for candidate in candidates:
            match, how = working.find(candidate)
            if match is None:
                story = _story_from(candidate, context.clock.now(), trust.get(candidate.source_id))
                working.add(story)
                created.append(story)
                assignments.setdefault(story.id, []).append(candidate.id)
                matched_by["new"] += 1
                continue

            _merge(match, candidate, trust.get(candidate.source_id))
            working.reindex(match)
            if match not in created:
                updated[match.id] = match
            assignments.setdefault(match.id, []).append(candidate.id)
            matched_by[how] += 1

        if context.dry_run:
            logger.info("resolve.dry_run", created=len(created), updated=len(updated), **matched_by)
        else:
            self._persist(context, created, list(updated.values()), assignments)

        clustered = sum(1 for story in working.stories if len(assignments.get(story.id, [])) > 1)
        return StageResult(
            items_in=len(candidates),
            items_out=len(created),
            meta={
                "candidates": len(candidates),
                "stories_created": len(created),
                "stories_updated": len(updated),
                "multi_mention_stories": clustered,
                "matched_by": matched_by,
                "similarity_threshold": self._similarity_threshold,
                "window_days": self._window_days,
                "dry_run": context.dry_run,
            },
        )

    def _persist(
        self,
        context: RunContext,
        created: list[Story],
        updated: list[Story],
        assignments: dict[str, list[str]],
    ) -> None:
        """One transaction: a story without its mentions is worse than neither."""
        with context.uow as uow:
            for story in created:
                uow.stories.add(story)
            for story in updated:
                uow.stories.update(story)
            for story_id, candidate_ids in assignments.items():
                attached = uow.candidates.assign_to_story(
                    context.workspace_id, story_id, candidate_ids
                )
                if attached != len(candidate_ids):
                    msg = (
                        f"expected to attach {len(candidate_ids)} candidate(s) to {story_id}, "
                        f"attached {attached}"
                    )
                    raise StageError(self.name, msg)
            uow.commit()


class _WorkingSet:
    """Stories available for matching, indexed by their clustering keys.

    Holds stories from the lookback window *and* the ones created during this
    run, so two mentions arriving in the same batch cluster together rather
    than becoming two stories that only a later run would notice.
    """

    def __init__(self, stories: list[Story], *, threshold: float) -> None:
        self.stories: list[Story] = []
        self._by_url: dict[str, Story] = {}
        self._by_title: dict[str, Story] = {}
        self._threshold = threshold
        for story in stories:
            self.add(story)

    def add(self, story: Story) -> None:
        self.stories.append(story)
        self._index(story)

    def reindex(self, story: Story) -> None:
        """Re-key a story whose headline or link changed during a merge."""
        self._index(story)

    def _index(self, story: Story) -> None:
        key = url_key(story.primary_url)
        # First writer wins: the earliest story with a key keeps it, so a later
        # mention joins the original rather than displacing it.
        if key and key not in self._by_url:
            self._by_url[key] = story
        title = title_key(story.canonical_headline)
        if title and title not in self._by_title:
            self._by_title[title] = story

    def find(self, candidate: StoryCandidate) -> tuple[Story | None, str]:
        """The story this mention belongs to, and which rule decided it."""
        key = url_key(candidate.primary_url)
        if key and (found := self._by_url.get(key)) is not None:
            return found, "url"

        title = title_key(candidate.headline)
        if title and (found := self._by_title.get(title)) is not None:
            return found, "title"

        best: Story | None = None
        best_score = self._threshold
        for story in self.stories:
            score = similarity(candidate.headline, story.canonical_headline)
            if score >= best_score:
                best, best_score = story, score
        if best is not None:
            return best, "similarity"
        return None, "new"


def _story_from(candidate: StoryCandidate, now: Any, source: Source | None) -> Story:
    """Open a story on its first mention.

    The founding mention's trust is recorded straight away. Without it, the
    *second* mention would always look more trustworthy than "no incumbent" and
    would overwrite the headline no matter which source it came from.
    """
    return Story(
        workspace_id=candidate.workspace_id,
        canonical_headline=candidate.headline,
        canonical_summary=candidate.summary,
        primary_url=candidate.primary_url,
        status=StoryStatus.RESOLVED,
        first_seen_at=candidate.extracted_at,
        last_seen_at=candidate.extracted_at,
        meta={
            "resolved_at": now.isoformat(),
            "headline_trust": source.trust_weight if source else 0.0,
        },
    )


def _merge(story: Story, candidate: StoryCandidate, source: Source | None) -> None:
    """Fold one more mention into a story.

    The canonical headline comes from the most trusted source that reported it,
    which is the first thing ``trust_weight`` has ever been used for: a wire
    service's wording should beat an aggregator's rewrite of it.
    """
    trust = source.trust_weight if source else 0.0
    incumbent = float(story.meta.get("headline_trust", 0.0))
    if trust > incumbent:
        story.canonical_headline = candidate.headline
        story.meta["headline_trust"] = trust
        if candidate.summary:
            story.canonical_summary = candidate.summary
    elif not story.canonical_summary and candidate.summary:
        story.canonical_summary = candidate.summary

    # A story known only by headline gains a link the moment any source
    # supplies one; that link is what enrichment follows.
    if not story.primary_url and candidate.primary_url:
        story.primary_url = candidate.primary_url

    story.first_seen_at = min(story.first_seen_at, candidate.extracted_at)
    story.last_seen_at = max(story.last_seen_at, candidate.extracted_at)
