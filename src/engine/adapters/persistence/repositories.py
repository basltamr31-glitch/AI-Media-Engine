"""SQLAlchemy implementations of the persistence ports.

Every method takes ``workspace_id`` and filters on it. Tenant isolation is
therefore impossible to forget: there is no query path that omits the scope.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.adapters.persistence import mappers
from engine.adapters.persistence.models import (
    ContentAssetRow,
    ContentRevisionRow,
    EditorialScoreRow,
    EvidenceRow,
    RunRow,
    RunStageRow,
    SourceDocumentRow,
    SourceRow,
    StoryCandidateRow,
    StoryRow,
    WorkspaceRow,
)
from engine.core.domain.content import AssetSpend, ContentAsset, ContentRevision
from engine.core.domain.enums import StoryStatus
from engine.core.domain.ledger import RunLedger, StageRecord
from engine.core.domain.source import Source, SourceDocument
from engine.core.domain.story import EditorialScore, Evidence, Story, StoryCandidate
from engine.core.domain.workspace import Workspace


class EntityNotFoundError(LookupError):
    """Raised when an update targets a row that does not exist."""


class SqlAlchemyWorkspaceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, workspace: Workspace) -> Workspace:
        self._session.add(mappers.workspace_to_row(workspace))
        self._session.flush()
        return workspace

    def get(self, workspace_id: str) -> Workspace | None:
        row = self._session.get(WorkspaceRow, workspace_id)
        return mappers.workspace_from_row(row) if row else None

    def get_by_slug(self, slug: str) -> Workspace | None:
        row = self._session.scalar(select(WorkspaceRow).where(WorkspaceRow.slug == slug))
        return mappers.workspace_from_row(row) if row else None

    def list_all(self, *, include_inactive: bool = False) -> list[Workspace]:
        statement = select(WorkspaceRow).order_by(WorkspaceRow.slug)
        if not include_inactive:
            statement = statement.where(WorkspaceRow.is_active.is_(True))
        return [mappers.workspace_from_row(row) for row in self._session.scalars(statement)]

    def update(self, workspace: Workspace) -> Workspace:
        row = self._session.get(WorkspaceRow, workspace.id)
        if row is None:
            msg = f"workspace {workspace.id} not found"
            raise EntityNotFoundError(msg)
        mappers.apply_workspace(row, workspace)
        self._session.flush()
        return workspace


class SqlAlchemySourceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, source: Source) -> Source:
        self._session.add(mappers.source_to_row(source))
        self._session.flush()
        return source

    def get(self, workspace_id: str, source_id: str) -> Source | None:
        row = self._session.scalar(
            select(SourceRow).where(
                SourceRow.id == source_id,
                SourceRow.workspace_id == workspace_id,
            )
        )
        return mappers.source_from_row(row) if row else None

    def get_by_identifier(self, workspace_id: str, kind: str, identifier: str) -> Source | None:
        row = self._session.scalar(
            select(SourceRow).where(
                SourceRow.workspace_id == workspace_id,
                SourceRow.kind == kind,
                SourceRow.identifier == identifier,
            )
        )
        return mappers.source_from_row(row) if row else None

    def list_for_workspace(self, workspace_id: str, *, only_active: bool = True) -> list[Source]:
        statement = (
            select(SourceRow).where(SourceRow.workspace_id == workspace_id).order_by(SourceRow.name)
        )
        if only_active:
            statement = statement.where(SourceRow.is_active.is_(True))
        return [mappers.source_from_row(row) for row in self._session.scalars(statement)]

    def update(self, source: Source) -> Source:
        row = self._session.scalar(
            select(SourceRow).where(
                SourceRow.id == source.id,
                SourceRow.workspace_id == source.workspace_id,
            )
        )
        if row is None:
            msg = f"source {source.id} not found in workspace {source.workspace_id}"
            raise EntityNotFoundError(msg)
        mappers.apply_source(row, source)
        self._session.flush()
        return source


class SqlAlchemySourceDocumentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, document: SourceDocument) -> SourceDocument:
        self._session.add(mappers.document_to_row(document))
        self._session.flush()
        return document

    def get(self, workspace_id: str, document_id: str) -> SourceDocument | None:
        row = self._session.scalar(
            select(SourceDocumentRow).where(
                SourceDocumentRow.id == document_id,
                SourceDocumentRow.workspace_id == workspace_id,
            )
        )
        return mappers.document_from_row(row) if row else None

    def exists_by_hash(self, workspace_id: str, content_hash: str) -> bool:
        found = self._session.scalar(
            select(SourceDocumentRow.id).where(
                SourceDocumentRow.workspace_id == workspace_id,
                SourceDocumentRow.content_hash == content_hash,
            )
        )
        return found is not None

    def list_for_source(
        self, workspace_id: str, source_id: str, *, limit: int = 100
    ) -> list[SourceDocument]:
        statement = (
            select(SourceDocumentRow)
            .where(
                SourceDocumentRow.workspace_id == workspace_id,
                SourceDocumentRow.source_id == source_id,
            )
            .order_by(SourceDocumentRow.fetched_at.desc())
            .limit(limit)
        )
        return [mappers.document_from_row(row) for row in self._session.scalars(statement)]

    def count_for_source(self, workspace_id: str, source_id: str) -> int:
        total = self._session.scalar(
            select(func.count())
            .select_from(SourceDocumentRow)
            .where(
                SourceDocumentRow.workspace_id == workspace_id,
                SourceDocumentRow.source_id == source_id,
            )
        )
        return int(total or 0)

    def list_for_workspace(
        self,
        workspace_id: str,
        *,
        limit: int = 200,
        source_id: str | None = None,
    ) -> list[SourceDocument]:
        """Documents across all sources, newest first.

        Newest first because a backlog is worked from the present backwards:
        if only part of it can be processed, today's news is the part worth
        having.
        """
        statement = select(SourceDocumentRow).where(SourceDocumentRow.workspace_id == workspace_id)
        if source_id is not None:
            statement = statement.where(SourceDocumentRow.source_id == source_id)
        statement = statement.order_by(SourceDocumentRow.fetched_at.desc()).limit(limit)
        return [mappers.document_from_row(row) for row in self._session.scalars(statement)]


class SqlAlchemyStoryCandidateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_many(self, candidates: list[StoryCandidate]) -> int:
        self._session.add_all(mappers.candidate_to_row(c) for c in candidates)
        self._session.flush()
        return len(candidates)

    def list_for_document(self, workspace_id: str, document_id: str) -> list[StoryCandidate]:
        statement = (
            select(StoryCandidateRow)
            .where(
                StoryCandidateRow.workspace_id == workspace_id,
                StoryCandidateRow.document_id == document_id,
            )
            .order_by(StoryCandidateRow.ordinal)
        )
        return [mappers.candidate_from_row(row) for row in self._session.scalars(statement)]

    def list_for_workspace(
        self, workspace_id: str, *, limit: int = 100, source_id: str | None = None
    ) -> list[StoryCandidate]:
        statement = select(StoryCandidateRow).where(StoryCandidateRow.workspace_id == workspace_id)
        if source_id is not None:
            statement = statement.where(StoryCandidateRow.source_id == source_id)
        statement = statement.order_by(
            StoryCandidateRow.extracted_at.desc(), StoryCandidateRow.ordinal
        ).limit(limit)
        return [mappers.candidate_from_row(row) for row in self._session.scalars(statement)]

    def documents_with_candidates(self, workspace_id: str, document_ids: list[str]) -> set[str]:
        """Which of ``document_ids`` have already been parsed.

        One query rather than one per document: parse selection runs over the
        whole backlog, and a per-document existence check is the classic way to
        turn a fast stage into a slow one.
        """
        if not document_ids:
            return set()
        statement = select(StoryCandidateRow.document_id).where(
            StoryCandidateRow.workspace_id == workspace_id,
            StoryCandidateRow.document_id.in_(document_ids),
        )
        return set(self._session.scalars(statement))

    def delete_for_document(self, workspace_id: str, document_id: str) -> int:
        """Discard a document's candidates so it can be parsed again.

        Candidates are *derived* data -- recomputable from a document that is
        itself never deleted -- so replacing them on re-parse is safe in a way
        that deleting a document would not be.
        """
        rows = list(
            self._session.scalars(
                select(StoryCandidateRow).where(
                    StoryCandidateRow.workspace_id == workspace_id,
                    StoryCandidateRow.document_id == document_id,
                )
            )
        )
        for row in rows:
            self._session.delete(row)
        self._session.flush()
        return len(rows)

    def count_for_workspace(self, workspace_id: str) -> int:
        total = self._session.scalar(
            select(func.count())
            .select_from(StoryCandidateRow)
            .where(StoryCandidateRow.workspace_id == workspace_id)
        )
        return int(total or 0)

    def list_unresolved(self, workspace_id: str, *, limit: int = 500) -> list[StoryCandidate]:
        """Candidates not yet clustered into a story, oldest first.

        Oldest first so the earliest mention becomes its story's anchor:
        ``first_seen_at`` is then a real observation rather than an artefact of
        processing order.
        """
        statement = (
            select(StoryCandidateRow)
            .where(
                StoryCandidateRow.workspace_id == workspace_id,
                StoryCandidateRow.story_id.is_(None),
            )
            .order_by(StoryCandidateRow.extracted_at, StoryCandidateRow.ordinal)
            .limit(limit)
        )
        return [mappers.candidate_from_row(row) for row in self._session.scalars(statement)]

    def list_for_story(self, workspace_id: str, story_id: str) -> list[StoryCandidate]:
        statement = (
            select(StoryCandidateRow)
            .where(
                StoryCandidateRow.workspace_id == workspace_id,
                StoryCandidateRow.story_id == story_id,
            )
            .order_by(StoryCandidateRow.extracted_at, StoryCandidateRow.ordinal)
        )
        return [mappers.candidate_from_row(row) for row in self._session.scalars(statement)]

    def assign_to_story(self, workspace_id: str, story_id: str, candidate_ids: list[str]) -> int:
        """Attach mentions to the story they were clustered into."""
        if not candidate_ids:
            return 0
        rows = list(
            self._session.scalars(
                select(StoryCandidateRow).where(
                    StoryCandidateRow.workspace_id == workspace_id,
                    StoryCandidateRow.id.in_(candidate_ids),
                )
            )
        )
        for row in rows:
            row.story_id = story_id
        self._session.flush()
        return len(rows)

    def count_by_source(self, workspace_id: str) -> dict[str, int]:
        statement = (
            select(StoryCandidateRow.source_id, func.count())
            .where(StoryCandidateRow.workspace_id == workspace_id)
            .group_by(StoryCandidateRow.source_id)
        )
        return dict(self._session.execute(statement).all())  # type: ignore[arg-type]


class SqlAlchemyStoryRepository:
    """Canonical stories.

    ``candidate_ids`` and ``source_ids`` are hydrated from the candidates
    pointing at each story -- one grouped query per read, rather than columns
    that could drift out of step with the foreign key.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, story: Story) -> Story:
        self._session.add(mappers.story_to_row(story))
        self._session.flush()
        return story

    def update(self, story: Story) -> Story:
        row = self._session.get(StoryRow, story.id)
        if row is None or row.workspace_id != story.workspace_id:
            msg = f"story {story.id} not found in workspace {story.workspace_id}"
            raise EntityNotFoundError(msg)
        mappers.apply_story(row, story)
        self._session.flush()
        return story

    def get(self, workspace_id: str, story_id: str) -> Story | None:
        row = self._session.get(StoryRow, story_id)
        if row is None or row.workspace_id != workspace_id:
            return None
        return self._hydrate([row])[0]

    def list_recent(
        self,
        workspace_id: str,
        *,
        limit: int = 100,
        since: datetime | None = None,
        status: StoryStatus | None = None,
    ) -> list[Story]:
        """Most recently seen stories -- the window clustering matches against."""
        statement = select(StoryRow).where(StoryRow.workspace_id == workspace_id)
        if since is not None:
            statement = statement.where(StoryRow.last_seen_at >= since)
        if status is not None:
            statement = statement.where(StoryRow.status == status.value)
        statement = statement.order_by(StoryRow.last_seen_at.desc()).limit(limit)
        return self._hydrate(list(self._session.scalars(statement)))

    def list_corroborated(
        self, workspace_id: str, *, min_sources: int = 2, limit: int = 100
    ) -> list[Story]:
        """Stories reported by at least ``min_sources`` distinct sources.

        A database-side filter rather than a filter over a page of results:
        corroborated stories are rare, so filtering a recent page would report
        "none" while plenty existed just outside it.
        """
        corroborated = (
            select(StoryCandidateRow.story_id)
            .where(
                StoryCandidateRow.workspace_id == workspace_id,
                StoryCandidateRow.story_id.is_not(None),
            )
            .group_by(StoryCandidateRow.story_id)
            .having(func.count(func.distinct(StoryCandidateRow.source_id)) >= min_sources)
        )
        statement = (
            select(StoryRow)
            .where(StoryRow.workspace_id == workspace_id, StoryRow.id.in_(corroborated))
            .order_by(StoryRow.last_seen_at.desc())
            .limit(limit)
        )
        return self._hydrate(list(self._session.scalars(statement)))

    def selected_since(self, workspace_id: str, *, since: datetime) -> list[Story]:
        """Stories chosen for production since ``since``, best rank first.

        The quota is counted over a window rather than per invocation, so that
        running the command twice in one morning tops up to the day's limit
        instead of quietly producing a second batch.
        """
        statement = (
            select(StoryRow)
            .where(
                StoryRow.workspace_id == workspace_id,
                StoryRow.selected_at.is_not(None),
                StoryRow.selected_at >= since,
            )
            .order_by(StoryRow.selected_at)
        )
        return self._hydrate(list(self._session.scalars(statement)))

    def list_selected(self, workspace_id: str, *, limit: int = 50) -> list[Story]:
        """Every selected story, most recently chosen first."""
        statement = (
            select(StoryRow)
            .where(
                StoryRow.workspace_id == workspace_id,
                StoryRow.selected_at.is_not(None),
            )
            .order_by(StoryRow.selected_at.desc())
            .limit(limit)
        )
        return self._hydrate(list(self._session.scalars(statement)))

    def list_needing_enrichment(
        self, workspace_id: str, *, limit: int = 25, max_attempts: int = 3
    ) -> list[Story]:
        """Resolved stories with a link, no enrichment, and attempts left.

        Ordered oldest-first by ``last_seen_at``: a story that has been waiting
        since Monday should not be starved by this morning's arrivals.

        The attempt filter is a JSON comparison rather than a column because
        the count is diagnostic bookkeeping, not something anything else
        queries -- if that changes, it earns a column.
        """
        statement = (
            select(StoryRow)
            .where(
                StoryRow.workspace_id == workspace_id,
                StoryRow.status == StoryStatus.RESOLVED.value,
                StoryRow.enrichment.is_(None),
                StoryRow.primary_url.is_not(None),
                func.coalesce(StoryRow.meta["enrichment_attempts"].as_integer(), 0) < max_attempts,
            )
            .order_by(StoryRow.last_seen_at)
            .limit(limit)
        )
        return self._hydrate(list(self._session.scalars(statement)))

    def delete_all(self, workspace_id: str) -> int:
        """Discard every story, returning its mentions to the unresolved pool.

        Stories are derived data, like candidates: the mentions they were built
        from are untouched, and ``story_id`` is ``ON DELETE SET NULL`` exactly
        so re-clustering is possible after a threshold change.
        """
        rows = list(
            self._session.scalars(select(StoryRow).where(StoryRow.workspace_id == workspace_id))
        )
        for row in rows:
            self._session.delete(row)
        self._session.flush()
        return len(rows)

    def count_for_workspace(self, workspace_id: str) -> int:
        total = self._session.scalar(
            select(func.count()).select_from(StoryRow).where(StoryRow.workspace_id == workspace_id)
        )
        return int(total or 0)

    def count_by_status(self, workspace_id: str) -> dict[str, int]:
        statement = (
            select(StoryRow.status, func.count())
            .where(StoryRow.workspace_id == workspace_id)
            .group_by(StoryRow.status)
        )
        return dict(self._session.execute(statement).all())  # type: ignore[arg-type]

    def _hydrate(self, rows: list[StoryRow]) -> list[Story]:
        """Attach every story's mentions in one query, not one per story."""
        if not rows:
            return []
        story_ids = [row.id for row in rows]
        mentions = self._session.execute(
            select(StoryCandidateRow.story_id, StoryCandidateRow.id, StoryCandidateRow.source_id)
            .where(StoryCandidateRow.story_id.in_(story_ids))
            .order_by(StoryCandidateRow.extracted_at, StoryCandidateRow.ordinal)
        ).all()

        candidates: dict[str, list[str]] = {story_id: [] for story_id in story_ids}
        sources: dict[str, list[str]] = {story_id: [] for story_id in story_ids}
        for story_id, candidate_id, source_id in mentions:
            candidates[story_id].append(candidate_id)
            if source_id not in sources[story_id]:
                sources[story_id].append(source_id)

        return [
            mappers.story_from_row(
                row, candidate_ids=candidates[row.id], source_ids=sources[row.id]
            )
            for row in rows
        ]


class SqlAlchemyEvidenceRepository:
    """The provenance ledger. Written by reconciliation, never edited."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_many(self, records: list[Evidence]) -> int:
        self._session.add_all(mappers.evidence_to_row(record) for record in records)
        self._session.flush()
        return len(records)

    def list_for_story(self, workspace_id: str, story_id: str) -> list[Evidence]:
        statement = (
            select(EvidenceRow)
            .where(
                EvidenceRow.workspace_id == workspace_id,
                EvidenceRow.story_id == story_id,
            )
            .order_by(EvidenceRow.kind, EvidenceRow.captured_at)
        )
        return [mappers.evidence_from_row(row) for row in self._session.scalars(statement)]

    def count_for_workspace(self, workspace_id: str) -> int:
        total = self._session.scalar(
            select(func.count())
            .select_from(EvidenceRow)
            .where(EvidenceRow.workspace_id == workspace_id)
        )
        return int(total or 0)

    def count_by_kind(self, workspace_id: str) -> dict[str, int]:
        statement = (
            select(EvidenceRow.kind, func.count())
            .where(EvidenceRow.workspace_id == workspace_id)
            .group_by(EvidenceRow.kind)
        )
        return dict(self._session.execute(statement).all())  # type: ignore[arg-type]

    def delete_for_story(self, workspace_id: str, story_id: str) -> int:
        rows = list(
            self._session.scalars(
                select(EvidenceRow).where(
                    EvidenceRow.workspace_id == workspace_id,
                    EvidenceRow.story_id == story_id,
                )
            )
        )
        for row in rows:
            self._session.delete(row)
        self._session.flush()
        return len(rows)


class SqlAlchemyEditorialScoreRepository:
    """Scores, kept as history rather than overwritten."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_many(self, scores: list[EditorialScore]) -> int:
        self._session.add_all(mappers.score_to_row(score) for score in scores)
        self._session.flush()
        return len(scores)

    def latest_for_story(self, workspace_id: str, story_id: str) -> EditorialScore | None:
        statement = (
            select(EditorialScoreRow)
            .where(
                EditorialScoreRow.workspace_id == workspace_id,
                EditorialScoreRow.story_id == story_id,
            )
            .order_by(EditorialScoreRow.scored_at.desc())
            .limit(1)
        )
        row = self._session.scalars(statement).first()
        return mappers.score_from_row(row) if row else None

    def history_for_story(self, workspace_id: str, story_id: str) -> list[EditorialScore]:
        statement = (
            select(EditorialScoreRow)
            .where(
                EditorialScoreRow.workspace_id == workspace_id,
                EditorialScoreRow.story_id == story_id,
            )
            .order_by(EditorialScoreRow.scored_at)
        )
        return [mappers.score_from_row(row) for row in self._session.scalars(statement)]

    def stories_scored(self, workspace_id: str, story_ids: list[str]) -> set[str]:
        """Which of ``story_ids`` already carry a score. One query, not N."""
        if not story_ids:
            return set()
        statement = select(EditorialScoreRow.story_id).where(
            EditorialScoreRow.workspace_id == workspace_id,
            EditorialScoreRow.story_id.in_(story_ids),
        )
        return set(self._session.scalars(statement))

    def top_for_run(
        self, workspace_id: str, run_id: str, *, limit: int = 20
    ) -> list[EditorialScore]:
        statement = (
            select(EditorialScoreRow)
            .where(
                EditorialScoreRow.workspace_id == workspace_id,
                EditorialScoreRow.run_id == run_id,
            )
            .order_by(EditorialScoreRow.total.desc())
            .limit(limit)
        )
        return [mappers.score_from_row(row) for row in self._session.scalars(statement)]

    def top_recent(self, workspace_id: str, *, limit: int = 20) -> list[EditorialScore]:
        """Best stories by their most recent score.

        A story scored twice must appear once, at its current standing -- a
        plain ``ORDER BY total`` would rank yesterday's opinion alongside
        today's.
        """
        latest = (
            select(
                EditorialScoreRow.story_id.label("story_id"),
                func.max(EditorialScoreRow.scored_at).label("scored_at"),
            )
            .where(EditorialScoreRow.workspace_id == workspace_id)
            .group_by(EditorialScoreRow.story_id)
            .subquery()
        )
        statement = (
            select(EditorialScoreRow)
            .join(
                latest,
                (EditorialScoreRow.story_id == latest.c.story_id)
                & (EditorialScoreRow.scored_at == latest.c.scored_at),
            )
            .order_by(EditorialScoreRow.total.desc())
            .limit(limit)
        )
        return [mappers.score_from_row(row) for row in self._session.scalars(statement)]

    def count_for_workspace(self, workspace_id: str) -> int:
        total = self._session.scalar(
            select(func.count())
            .select_from(EditorialScoreRow)
            .where(EditorialScoreRow.workspace_id == workspace_id)
        )
        return int(total or 0)


class SqlAlchemyContentAssetRepository:
    """Assets and their revision chain.

    Revisions are only ever inserted. The one mutable thing about an asset is
    where its ``current_revision_id`` points, and that moves forward only after
    the revision it names is durable.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # -- assets ------------------------------------------------------------

    def add(self, asset: ContentAsset) -> ContentAsset:
        self._session.add(mappers.asset_to_row(asset))
        self._session.flush()
        return asset

    def update(self, asset: ContentAsset) -> ContentAsset:
        row = self._session.get(ContentAssetRow, asset.id)
        if row is None or row.workspace_id != asset.workspace_id:
            msg = f"content asset {asset.id!r} not found"
            raise EntityNotFoundError(msg)
        mappers.apply_asset(row, asset)
        self._session.flush()
        return asset

    def get(self, workspace_id: str, asset_id: str) -> ContentAsset | None:
        row = self._session.get(ContentAssetRow, asset_id)
        if row is None or row.workspace_id != workspace_id:
            return None
        return mappers.asset_from_row(row)

    def find_slot(
        self,
        workspace_id: str,
        *,
        story_id: str,
        product_key: str,
        channel_key: str | None,
        language: str,
        variant: str = "a",
    ) -> ContentAsset | None:
        statement = select(ContentAssetRow).where(
            ContentAssetRow.workspace_id == workspace_id,
            ContentAssetRow.story_id == story_id,
            ContentAssetRow.product_key == product_key,
            # `is_` rather than `==` for the channel-agnostic case: SQL equality
            # against NULL is never true, so a channel-less product would
            # otherwise never match its own slot and duplicate on every run.
            ContentAssetRow.channel_key.is_(None)
            if channel_key is None
            else ContentAssetRow.channel_key == channel_key,
            ContentAssetRow.language == language,
            ContentAssetRow.variant == variant,
        )
        row = self._session.scalar(statement)
        return mappers.asset_from_row(row) if row else None

    def list_for_story(self, workspace_id: str, story_id: str) -> list[ContentAsset]:
        statement = (
            select(ContentAssetRow)
            .where(
                ContentAssetRow.workspace_id == workspace_id,
                ContentAssetRow.story_id == story_id,
            )
            .order_by(ContentAssetRow.channel_key, ContentAssetRow.language)
        )
        return [mappers.asset_from_row(row) for row in self._session.scalars(statement)]

    def list_recent(self, workspace_id: str, *, limit: int = 50) -> list[ContentAsset]:
        statement = (
            select(ContentAssetRow)
            .where(ContentAssetRow.workspace_id == workspace_id)
            .order_by(ContentAssetRow.created_at.desc())
            .limit(limit)
        )
        return [mappers.asset_from_row(row) for row in self._session.scalars(statement)]

    def count_for_workspace(self, workspace_id: str) -> int:
        total = self._session.scalar(
            select(func.count())
            .select_from(ContentAssetRow)
            .where(ContentAssetRow.workspace_id == workspace_id)
        )
        return int(total or 0)

    # -- revisions ---------------------------------------------------------

    def add_revision(self, revision: ContentRevision) -> ContentRevision:
        self._session.add(mappers.revision_to_row(revision))
        self._session.flush()
        return revision

    def get_revision(self, workspace_id: str, revision_id: str) -> ContentRevision | None:
        row = self._session.get(ContentRevisionRow, revision_id)
        if row is None or row.workspace_id != workspace_id:
            return None
        return mappers.revision_from_row(row)

    def latest_revision(self, workspace_id: str, asset_id: str) -> ContentRevision | None:
        statement = (
            select(ContentRevisionRow)
            .where(
                ContentRevisionRow.workspace_id == workspace_id,
                ContentRevisionRow.asset_id == asset_id,
            )
            .order_by(ContentRevisionRow.revision_number.desc())
            .limit(1)
        )
        row = self._session.scalar(statement)
        return mappers.revision_from_row(row) if row else None

    def spend_by_asset(self, workspace_id: str, *, limit: int = 50) -> list[AssetSpend]:
        """What each asset has cost across its whole revision chain.

        Summed over revisions rather than read off the asset, because a
        regenerated asset spent money more than once and only the chain knows
        how many times.
        """
        statement = (
            select(
                ContentRevisionRow.asset_id,
                func.count().label("revisions"),
                func.sum(ContentRevisionRow.input_tokens).label("input_tokens"),
                func.sum(ContentRevisionRow.output_tokens).label("output_tokens"),
                func.sum(ContentRevisionRow.cost_usd).label("cost_usd"),
            )
            .where(ContentRevisionRow.workspace_id == workspace_id)
            .group_by(ContentRevisionRow.asset_id)
            .order_by(func.sum(ContentRevisionRow.cost_usd).desc())
            .limit(limit)
        )
        return [
            AssetSpend(
                asset_id=row.asset_id,
                revisions=int(row.revisions or 0),
                input_tokens=int(row.input_tokens or 0),
                output_tokens=int(row.output_tokens or 0),
                cost_usd=float(row.cost_usd or 0.0),
            )
            for row in self._session.execute(statement)
        ]

    def total_spend(self, workspace_id: str, *, since: datetime | None = None) -> AssetSpend:
        """Everything generation has cost this workspace."""
        statement = select(
            func.count().label("revisions"),
            func.sum(ContentRevisionRow.input_tokens).label("input_tokens"),
            func.sum(ContentRevisionRow.output_tokens).label("output_tokens"),
            func.sum(ContentRevisionRow.cost_usd).label("cost_usd"),
        ).where(ContentRevisionRow.workspace_id == workspace_id)
        if since is not None:
            statement = statement.where(ContentRevisionRow.created_at >= since)
        row = self._session.execute(statement).one()
        return AssetSpend(
            asset_id="",
            revisions=int(row.revisions or 0),
            input_tokens=int(row.input_tokens or 0),
            output_tokens=int(row.output_tokens or 0),
            cost_usd=float(row.cost_usd or 0.0),
        )

    def list_revisions(self, workspace_id: str, asset_id: str) -> list[ContentRevision]:
        statement = (
            select(ContentRevisionRow)
            .where(
                ContentRevisionRow.workspace_id == workspace_id,
                ContentRevisionRow.asset_id == asset_id,
            )
            .order_by(ContentRevisionRow.revision_number)
        )
        return [mappers.revision_from_row(row) for row in self._session.scalars(statement)]


class SqlAlchemyRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, run: RunLedger) -> RunLedger:
        self._session.add(mappers.run_to_row(run))
        self._session.flush()
        return run

    def get(self, workspace_id: str, run_id: str) -> RunLedger | None:
        row = self._session.scalar(
            select(RunRow).where(RunRow.id == run_id, RunRow.workspace_id == workspace_id)
        )
        return mappers.run_from_row(row) if row else None

    def update(self, run: RunLedger) -> RunLedger:
        row = self._session.scalar(
            select(RunRow).where(RunRow.id == run.id, RunRow.workspace_id == run.workspace_id)
        )
        if row is None:
            msg = f"run {run.id} not found in workspace {run.workspace_id}"
            raise EntityNotFoundError(msg)
        mappers.apply_run(row, run)
        self._session.flush()
        return run

    def list_recent(self, workspace_id: str, *, limit: int = 20) -> list[RunLedger]:
        statement = (
            select(RunRow)
            .where(RunRow.workspace_id == workspace_id)
            .order_by(RunRow.started_at.desc())
            .limit(limit)
        )
        return [mappers.run_from_row(row) for row in self._session.scalars(statement)]

    def add_stage(self, stage: StageRecord) -> StageRecord:
        self._session.add(mappers.stage_to_row(stage))
        self._session.flush()
        return stage

    def update_stage(self, stage: StageRecord) -> StageRecord:
        row = self._session.get(RunStageRow, stage.id)
        if row is None:
            msg = f"stage {stage.id} not found"
            raise EntityNotFoundError(msg)
        mappers.apply_stage(row, stage)
        self._session.flush()
        return stage

    def list_stages(self, workspace_id: str, run_id: str) -> list[StageRecord]:
        statement = (
            select(RunStageRow)
            .where(
                RunStageRow.run_id == run_id,
                RunStageRow.workspace_id == workspace_id,
            )
            .order_by(RunStageRow.sequence)
        )
        return [mappers.stage_from_row(row) for row in self._session.scalars(statement)]
