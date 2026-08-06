"""Persistence round-trips and tenant isolation."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from engine.adapters.persistence.repositories import EntityNotFoundError
from engine.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWork
from engine.core.domain.enums import Language, SourceKind
from engine.core.domain.source import Source, SourceDocument, compute_content_hash
from engine.core.domain.workspace import Workspace

pytestmark = pytest.mark.integration


class TestWorkspaceRepository:
    def test_round_trip_preserves_every_field(
        self, uow: SqlAlchemyUnitOfWork, workspace: Workspace
    ):
        with uow as active:
            active.workspaces.add(workspace)
            active.commit()

        with uow as active:
            loaded = active.workspaces.get_by_slug(workspace.slug)

        assert loaded is not None
        assert loaded.id == workspace.id
        assert loaded.name == workspace.name
        assert loaded.timezone == workspace.timezone
        assert loaded.languages == [Language.AR, Language.EN]

    def test_slug_is_unique(self, uow: SqlAlchemyUnitOfWork, workspace: Workspace):
        with uow as active:
            active.workspaces.add(workspace)
            active.commit()

        with pytest.raises(IntegrityError), uow as active:
            active.workspaces.add(
                Workspace(account_id=workspace.account_id, slug=workspace.slug, name="Duplicate")
            )
            active.commit()

    def test_update_of_missing_row_is_reported(
        self, uow: SqlAlchemyUnitOfWork, workspace: Workspace
    ):
        with pytest.raises(EntityNotFoundError), uow as active:
            active.workspaces.update(workspace)


class TestSourceRepository:
    def test_round_trip(self, uow: SqlAlchemyUnitOfWork, source: Source):
        with uow as active:
            active.sources.add(source)
            active.commit()

        with uow as active:
            loaded = active.sources.get(source.workspace_id, source.id)

        assert loaded is not None
        assert loaded.kind is SourceKind.RSS
        assert loaded.parser_key == "arxiv_rss"
        assert loaded.trust_weight == pytest.approx(0.75)

    def test_lookup_is_scoped_to_the_workspace(self, uow: SqlAlchemyUnitOfWork, source: Source):
        """Tenant isolation: another workspace's ID must not resolve the row."""
        with uow as active:
            active.sources.add(source)
            active.commit()

        with uow as active:
            assert active.sources.get("ws_someone_else", source.id) is None


class TestSourceDocumentRepository:
    def test_content_hash_prevents_double_ingestion(
        self, uow: SqlAlchemyUnitOfWork, source: Source
    ):
        with uow as active:
            active.sources.add(source)
            active.commit()

        digest = compute_content_hash("TLDR AI", "Body of the issue")
        document = SourceDocument(
            workspace_id=source.workspace_id,
            source_id=source.id,
            content_hash=digest,
            title="TLDR AI",
            raw_content="Body of the issue",
        )

        with uow as active:
            assert not active.documents.exists_by_hash(source.workspace_id, digest)
            active.documents.add(document)
            active.commit()

        with uow as active:
            assert active.documents.exists_by_hash(source.workspace_id, digest)


class TestTransactionSemantics:
    def test_uncommitted_work_is_discarded(self, uow: SqlAlchemyUnitOfWork, workspace: Workspace):
        with uow as active:
            active.workspaces.add(workspace)
            # deliberately no commit

        with uow as active:
            assert active.workspaces.get_by_slug(workspace.slug) is None

    def test_exception_rolls_back(self, uow: SqlAlchemyUnitOfWork, workspace: Workspace):
        with pytest.raises(RuntimeError), uow as active:
            active.workspaces.add(workspace)
            msg = "boom"
            raise RuntimeError(msg)

        with uow as active:
            assert active.workspaces.get_by_slug(workspace.slug) is None

    def test_session_is_unavailable_outside_the_context(self, uow: SqlAlchemyUnitOfWork):
        with pytest.raises(RuntimeError, match="not active"):
            _ = uow.session
