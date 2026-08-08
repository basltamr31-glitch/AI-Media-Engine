"""Evidence stage -- record what supports each story.

The provenance ledger. For every story it writes one record per source that
reported it, plus one for the page enrichment actually read. Three things
depend on this being data rather than a join someone remembers to write:

* **A legal defence.** "Where did this sentence come from?" answered from
  storage, months later, without re-fetching anything.
* **A trust feature.** The sources behind a story are shown, not implied.
* **A debugging tool.** A story supported only by aggregators is one the
  engine should not be writing about -- and that is now a query.

Phase 2 records **provenance** evidence: the claim is the story's own
headline, supported by the sources that carried it. Phase 3 adds **claim-level**
evidence, binding a generated sentence to the passage that supports it. Same
table, same kinds; only the granularity changes.

Evidence is derived data, reconciled rather than appended: running twice adds
nothing, and a story that gains a mention gains exactly one record.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlsplit

from engine.core.domain.enums import EvidenceKind
from engine.core.domain.matching import url_key
from engine.core.domain.source import Source
from engine.core.domain.story import Evidence, Story, StoryCandidate
from engine.observability.logging import get_logger
from engine.pipeline.context import RunContext
from engine.pipeline.stages.base import StageResult

logger = get_logger(__name__)

#: Config key by which a source declares what its links are worth as evidence.
EVIDENCE_KIND_KEY = "evidence_kind"


class EvidenceStage:
    """Reconciles each story's evidence with its mentions and enrichment."""

    def __init__(self, *, max_stories: int = 200, window_days: int = 7) -> None:
        self._max_stories = max_stories
        self._window_days = window_days

    @property
    def name(self) -> str:
        return "evidence"

    def run(self, context: RunContext) -> StageResult:
        since = context.clock.now() - timedelta(days=self._window_days)
        with context.uow as uow:
            stories = uow.stories.list_recent(
                context.workspace_id, limit=self._max_stories, since=since
            )
            if not stories:
                return StageResult.skip("no stories in the window")

            sources = {
                source.id: source
                for source in uow.sources.list_for_workspace(
                    context.workspace_id, only_active=False
                )
            }
            mentions = {
                story.id: uow.candidates.list_for_story(context.workspace_id, story.id)
                for story in stories
            }
            recorded = {
                story.id: {
                    (record.kind, record.source_url)
                    for record in uow.evidence.list_for_story(context.workspace_id, story.id)
                }
                for story in stories
            }

        new_records: list[Evidence] = []
        by_kind: dict[str, int] = {}
        for story in stories:
            for record in build_evidence(story, mentions[story.id], sources):
                fingerprint = (record.kind, record.source_url)
                if fingerprint in recorded[story.id]:
                    continue
                recorded[story.id].add(fingerprint)
                new_records.append(record)
                by_kind[record.kind.value] = by_kind.get(record.kind.value, 0) + 1

        if not context.dry_run and new_records:
            with context.uow as uow:
                uow.evidence.add_many(new_records)
                uow.commit()

        # The number that matters editorially: a story nobody first-hand
        # reported is one we should not be writing about.
        aggregator_only = sum(
            1
            for story in stories
            if recorded[story.id]
            and all(kind is EvidenceKind.AGGREGATOR for kind, _ in recorded[story.id])
        )
        logger.info(
            "evidence.reconciled",
            stories=len(stories),
            recorded=len(new_records),
            aggregator_only=aggregator_only,
        )
        return StageResult(
            items_in=len(stories),
            items_out=len(new_records),
            meta={
                "stories": len(stories),
                "recorded": len(new_records),
                "by_kind": by_kind,
                "aggregator_only_stories": aggregator_only,
                "dry_run": context.dry_run,
            },
        )


def build_evidence(
    story: Story,
    mentions: list[StoryCandidate],
    sources: dict[str, Source],
) -> list[Evidence]:
    """Every record this story's current state justifies.

    The claim is the story's own headline: at this granularity that *is* the
    assertion the sources support.
    """
    records = [
        Evidence(
            workspace_id=story.workspace_id,
            story_id=story.id,
            claim=story.canonical_headline,
            kind=kind_of(mention, sources.get(mention.source_id)),
            source_url=mention.primary_url or "",
            source_title=(source.name if (source := sources.get(mention.source_id)) else None),
            captured_at=mention.extracted_at,
        )
        for mention in mentions
        if mention.primary_url
    ]

    if story.enrichment is not None:
        records.append(
            Evidence(
                workspace_id=story.workspace_id,
                story_id=story.id,
                claim=story.canonical_headline,
                kind=_enrichment_kind(story, mentions, sources),
                source_url=story.enrichment.url,
                source_title=story.enrichment.title,
                captured_at=story.enrichment.fetched_at,
            )
        )
    return records


def kind_of(mention: StoryCandidate, source: Source | None) -> EvidenceKind:
    """What one source's link is worth as evidence.

    Declared per source via ``config.evidence_kind`` where the answer is known
    -- arXiv publishes the authors' own words, so its links are primary.

    Inferred otherwise from a rule that is deliberately conservative: **a
    source linking to itself has published something; a source linking
    elsewhere is pointing at someone else's work.** The first is at least
    secondary, the second no better than aggregation. Nothing is called
    ``primary`` by inference, because whether a page is the actor's own
    announcement or a journalist's report is not something a URL can tell us.
    """
    if source is None:
        return EvidenceKind.AGGREGATOR

    declared = source.config.get(EVIDENCE_KIND_KEY)
    if isinstance(declared, str):
        try:
            return EvidenceKind(declared.casefold())
        except ValueError:
            logger.warning("evidence.unknown_kind", source=source.name, declared=declared)

    if _same_host(mention.primary_url, source.identifier):
        return EvidenceKind.SECONDARY
    return EvidenceKind.AGGREGATOR


def _enrichment_kind(
    story: Story,
    mentions: list[StoryCandidate],
    sources: dict[str, Source],
) -> EvidenceKind:
    """How to rate the page we read ourselves.

    Primary only if some source told us that link is a primary source;
    otherwise secondary. Having read a page first-hand rules out treating it as
    mere aggregation, but it does not make a news report into an announcement.
    """
    if story.enrichment is None:  # pragma: no cover -- guarded by the caller
        return EvidenceKind.SECONDARY

    target = url_key(story.enrichment.url)
    for mention in mentions:
        if url_key(mention.primary_url) != target:
            continue
        if kind_of(mention, sources.get(mention.source_id)) is EvidenceKind.PRIMARY:
            return EvidenceKind.PRIMARY
    return EvidenceKind.SECONDARY


def _same_host(url: str | None, identifier: str) -> bool:
    left, right = _host_of(url), _host_of(identifier)
    if not left or not right:
        return False
    return left == right or left.endswith(f".{right}") or right.endswith(f".{left}")


def _host_of(value: str | None) -> str:
    """Host of a URL, or of a bare hostname. Email addresses have none."""
    if not value or "@" in value:
        return ""
    parts = urlsplit(value if "//" in value else f"//{value}")
    return parts.netloc.casefold().removeprefix("www.").split(":", 1)[0]
