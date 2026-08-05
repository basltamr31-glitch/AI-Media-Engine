"""Unit of work: one transaction, one consistent set of repositories.

A pipeline run touches several tables (documents, stories, ledger). Committing
them independently would leave the ledger claiming work that the data does not
reflect. The unit of work makes "all of it or none of it" the default.
"""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from engine.adapters.persistence.repositories import (
    SqlAlchemyRunRepository,
    SqlAlchemySourceDocumentRepository,
    SqlAlchemySourceRepository,
    SqlAlchemyWorkspaceRepository,
)


class SqlAlchemyUnitOfWork:
    """Transactional scope implementing :class:`engine.core.ports.UnitOfWork`.

    Usage::

        with SqlAlchemyUnitOfWork(session_factory) as uow:
            uow.workspaces.add(workspace)
            uow.commit()

    Leaving the block without calling ``commit()`` rolls back. Explicit commits
    make the write boundary visible at the call site rather than implicit.
    """

    # Declared here rather than only assigned in __enter__, so the attributes
    # are part of the published interface and accessing one outside the context
    # manager is an obvious AttributeError rather than a mystery.
    workspaces: SqlAlchemyWorkspaceRepository
    sources: SqlAlchemySourceRepository
    documents: SqlAlchemySourceDocumentRepository
    runs: SqlAlchemyRunRepository

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        self._session = self._session_factory()
        self.workspaces = SqlAlchemyWorkspaceRepository(self._session)
        self.sources = SqlAlchemySourceRepository(self._session)
        self.documents = SqlAlchemySourceDocumentRepository(self._session)
        self.runs = SqlAlchemyRunRepository(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None:
                self.rollback()
        finally:
            if self._session is not None:
                self._session.close()
                self._session = None

    @property
    def session(self) -> Session:
        """The active session. Raises if used outside the context manager."""
        if self._session is None:
            msg = "unit of work is not active; use it as a context manager"
            raise RuntimeError(msg)
        return self._session

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
