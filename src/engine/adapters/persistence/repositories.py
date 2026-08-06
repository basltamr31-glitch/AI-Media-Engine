"""SQLAlchemy implementations of the persistence ports.

Every method takes ``workspace_id`` and filters on it. Tenant isolation is
therefore impossible to forget: there is no query path that omits the scope.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.adapters.persistence import mappers
from engine.adapters.persistence.models import (
    RunRow,
    RunStageRow,
    SourceDocumentRow,
    SourceRow,
    WorkspaceRow,
)
from engine.core.domain.ledger import RunLedger, StageRecord
from engine.core.domain.source import Source, SourceDocument
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
