"""Database engine and session construction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from engine.settings import Settings


def sqlite_file_path(database_url: str) -> Path | None:
    """Extract the on-disk path from a SQLite URL, if it is a file-backed one."""
    marker = ":///"
    if not database_url.startswith("sqlite") or marker not in database_url:
        return None
    raw_path = database_url.split(marker, 1)[1]
    if not raw_path or raw_path == ":memory:":
        return None
    return Path(raw_path)


def ensure_sqlite_directory(database_url: str) -> None:
    """Create the parent directory of a file-backed SQLite database.

    Shared with the Alembic environment: migrations connect directly rather than
    through :func:`create_db_engine`, and a first-ever ``db upgrade`` would
    otherwise fail on a missing ``data/`` directory.
    """
    db_path = sqlite_file_path(database_url)
    if db_path is not None:
        db_path.parent.mkdir(parents=True, exist_ok=True)


def create_db_engine(settings: Settings) -> Engine:
    """Build the SQLAlchemy engine for the configured database.

    SQLite needs two things that Postgres gives for free, applied here so the
    rest of the codebase can stay backend-agnostic:

    * ``PRAGMA foreign_keys=ON`` -- SQLite ignores foreign keys by default,
      which would let the tenant hierarchy silently develop orphan rows.
    * ``PRAGMA journal_mode=WAL`` -- allows a reader (an inspection command)
      concurrently with a writer (a running pipeline).
    """
    connect_args: dict[str, Any] = {}
    if settings.is_sqlite:
        ensure_sqlite_directory(settings.database_url)
        connect_args["check_same_thread"] = False

    db_engine = create_engine(
        settings.database_url,
        echo=settings.database_echo,
        future=True,
        pool_pre_ping=True,  # survives idle-connection drops on hosted Postgres
        connect_args=connect_args,
    )

    if settings.is_sqlite:

        @event.listens_for(db_engine, "connect")
        def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return db_engine


def create_session_factory(db_engine: Engine) -> sessionmaker[Session]:
    """Build a session factory.

    ``expire_on_commit=False`` because repositories return detached domain
    objects; nothing should trigger a lazy reload after the transaction closes.
    """
    return sessionmaker(bind=db_engine, expire_on_commit=False, future=True)
