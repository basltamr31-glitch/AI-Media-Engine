"""Persistence round-trips and tenant isolation."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from engine.adapters.persistence.repositories import EntityNotFoundError
from engine.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWork
from engine.core.domain.enums import SourceKind
from engine.core.domain.language import ARABIC, ENGLISH
from engine.core.domain.source import Source, SourceDocument, compute_content_hash
from engine.core.domain.story import StoryCandidate
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
        assert loaded.languages == [ARABIC, ENGLISH]

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


class TestStoryCandidateRepository:
    """Candidates are derived data: replaceable, unlike the documents they come from."""

    def _document(self, source: Source, suffix: str = "") -> SourceDocument:
        return SourceDocument(
            workspace_id=source.workspace_id,
            source_id=source.id,
            content_hash=compute_content_hash("issue", suffix),
            title=f"Issue {suffix}",
            raw_content="Body",
        )

    def _candidates(self, document: SourceDocument, count: int) -> list[StoryCandidate]:
        return [
            StoryCandidate(
                workspace_id=document.workspace_id,
                document_id=document.id,
                source_id=document.source_id,
                headline=f"Story {index}",
                primary_url=f"https://example.test/{index}",
                ordinal=index,
            )
            for index in range(count)
        ]

    @pytest.fixture
    def stored(self, uow: SqlAlchemyUnitOfWork, source: Source):
        document = self._document(source, "a")
        with uow as active:
            active.sources.add(source)
            active.documents.add(document)
            active.candidates.add_many(self._candidates(document, 3))
            active.commit()
        return document

    def test_candidates_round_trip(self, uow: SqlAlchemyUnitOfWork, source, stored):
        with uow as active:
            candidates = active.candidates.list_for_document(source.workspace_id, stored.id)
        assert [c.headline for c in candidates] == ["Story 0", "Story 1", "Story 2"]
        assert candidates[0].primary_url == "https://example.test/0"

    def test_they_come_back_in_document_order(self, uow: SqlAlchemyUnitOfWork, source, stored):
        """Newsletters lead with what they rank highest, so order is information."""
        with uow as active:
            candidates = active.candidates.list_for_document(source.workspace_id, stored.id)
        assert [c.ordinal for c in candidates] == [0, 1, 2]

    def test_parsed_documents_are_found_in_one_query(
        self, uow: SqlAlchemyUnitOfWork, source, stored
    ):
        unparsed = self._document(source, "b")
        with uow as active:
            active.documents.add(unparsed)
            active.commit()

        with uow as active:
            found = active.candidates.documents_with_candidates(
                source.workspace_id, [stored.id, unparsed.id]
            )
        assert found == {stored.id}

    def test_an_empty_id_list_asks_the_database_nothing(
        self, uow: SqlAlchemyUnitOfWork, source, stored
    ):
        with uow as active:
            assert active.candidates.documents_with_candidates(source.workspace_id, []) == set()

    def test_deleting_makes_room_for_a_re_parse(self, uow: SqlAlchemyUnitOfWork, source, stored):
        """Same ordinals again would violate the unique constraint otherwise."""
        with uow as active:
            assert active.candidates.delete_for_document(source.workspace_id, stored.id) == 3
            active.candidates.add_many(self._candidates(stored, 2))
            active.commit()

        with uow as active:
            assert active.candidates.count_for_workspace(source.workspace_id) == 2

    def test_counts_are_grouped_by_source(self, uow: SqlAlchemyUnitOfWork, source, stored):
        with uow as active:
            assert active.candidates.count_by_source(source.workspace_id) == {source.id: 3}

    def test_a_reused_ordinal_is_rejected(self, uow: SqlAlchemyUnitOfWork, source, stored):
        """Duplicate candidates would double-count corroboration later."""
        with uow as active, pytest.raises(IntegrityError):
            active.candidates.add_many(self._candidates(stored, 1))
            active.commit()

    def test_another_workspace_sees_nothing(self, uow: SqlAlchemyUnitOfWork, source, stored):
        with uow as active:
            assert active.candidates.count_for_workspace("ws_someone_else") == 0


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
