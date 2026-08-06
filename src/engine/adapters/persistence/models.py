"""SQLAlchemy table definitions.

Deliberate constraints on this schema:

* **Postgres-compatible subset only.** No SQLite-specific types or behaviour, so
  moving to a hosted database is a connection-string change. JSON columns use a
  variant that becomes ``JSONB`` on Postgres automatically.
* **Explicit constraint naming.** Alembic cannot reliably alter unnamed
  constraints on SQLite, and unnamed constraints make production migrations a
  gamble.
* **``workspace_id`` on every table, indexed.** Tenant isolation is a schema
  property, not a convention someone has to remember.
* **Enums stored as strings.** Native database enums require a migration to add
  a value, which is a poor trade for a domain still finding its shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Portable JSON: plain JSON on SQLite, JSONB (indexable, binary) on Postgres.
JsonType: Final = JSON().with_variant(JSONB(), "postgresql")

# Predictable constraint names make migrations deterministic across backends.
NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

ID_LENGTH: Final = 40  # "<prefix>_<26-char ULID>" with headroom


class Base(DeclarativeBase):
    """Declarative base carrying the naming convention and type mappings."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {  # noqa: RUF012 - SQLAlchemy API requires a plain dict
        dict[str, Any]: JsonType,
        list[str]: JsonType,
        datetime: DateTime(timezone=True),
    }


class WorkspaceRow(Base):
    """Tenant root."""

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    languages: Mapped[list[str]] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    __table_args__ = (UniqueConstraint("slug", name="uq_workspaces_slug"),)


class SourceRow(Base):
    """A configured input feed."""

    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    identifier: Mapped[str] = mapped_column(String(512), nullable=False)
    parser_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trust_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        # The NAME is the natural key within a workspace, not the identifier.
        #
        # Two reasons, both learned the hard way in Phase 1:
        #  1. An identifier is a mutable property of a source (arXiv moved from
        #     http to https). Keying on it forks a second source on any URL
        #     change and orphans the original's ingestion history.
        #  2. The same endpoint legitimately appears twice under different
        #     names -- one GitHub releases URL filtered two ways, one HN query
        #     at two attention thresholds. Uniqueness on identifier forbids it.
        UniqueConstraint("workspace_id", "name", name="uq_sources_workspace_id_name"),
        Index("ix_sources_workspace_id", "workspace_id"),
        # Non-unique: still worth indexing for lookups, no longer a constraint.
        Index("ix_sources_workspace_id_identifier", "workspace_id", "identifier"),
    )


class SourceDocumentRow(Base):
    """One immutable fetched artifact."""

    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False, default="text/plain")
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    __table_args__ = (
        # Content-hash uniqueness is what makes ingestion safely re-runnable.
        UniqueConstraint(
            "workspace_id", "content_hash", name="uq_source_documents_workspace_id_content_hash"
        ),
        Index("ix_source_documents_workspace_id_source_id", "workspace_id", "source_id"),
        Index("ix_source_documents_fetched_at", "fetched_at"),
    )


class RunRow(Base):
    """One pipeline execution."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    pipeline: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="cli")
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    __table_args__ = (Index("ix_runs_workspace_id_started_at", "workspace_id", "started_at"),)


class RunStageRow(Base):
    """One stage within a pipeline execution."""

    __tablename__ = "run_stages"

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    items_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_run_stages_run_id_sequence"),
        Index("ix_run_stages_workspace_id", "workspace_id"),
    )
