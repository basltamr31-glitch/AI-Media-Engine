"""SQLAlchemy persistence adapter."""

from __future__ import annotations

from engine.adapters.persistence.models import Base
from engine.adapters.persistence.repositories import (
    EntityNotFoundError,
    SqlAlchemyRunRepository,
    SqlAlchemySourceDocumentRepository,
    SqlAlchemySourceRepository,
    SqlAlchemyWorkspaceRepository,
)
from engine.adapters.persistence.session import create_db_engine, create_session_factory
from engine.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWork

__all__ = [
    "Base",
    "EntityNotFoundError",
    "SqlAlchemyRunRepository",
    "SqlAlchemySourceDocumentRepository",
    "SqlAlchemySourceRepository",
    "SqlAlchemyUnitOfWork",
    "SqlAlchemyWorkspaceRepository",
    "create_db_engine",
    "create_session_factory",
]
