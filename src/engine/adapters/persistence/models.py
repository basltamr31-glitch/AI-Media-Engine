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

from datetime import UTC, datetime
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """A datetime column that always returns timezone-aware UTC.

    SQLite has no native timezone support, so it hands back *naive* datetimes
    even from a ``DateTime(timezone=True)`` column. Mixing those with the
    clock's aware values raises ``can't subtract offset-naive and offset-aware
    datetimes`` at the first arithmetic -- and where it does not raise, it
    compares wrong silently, which is worse.

    Fixed once here rather than in each mapper: every datetime in the system
    crosses this boundary, and a rule enforced in twenty places is a rule that
    will be missed in the twenty-first.

    Naive values going *in* are assumed UTC. Nothing in the engine produces
    local time -- the ``DTZ`` lint rule forbids it -- so this normalises
    hand-written fixtures and legacy rows rather than guessing at a timezone.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# Portable JSON: plain JSON on SQLite, JSONB (indexable, binary) on Postgres.
JsonType: Final = JSON().with_variant(JSONB(), "postgresql")

#: For *optional* JSON columns. Without ``none_as_null`` SQLAlchemy stores
#: Python ``None`` as the JSON value ``null``, which is not SQL NULL -- so
#: ``IS NULL`` silently matches nothing and "which rows lack this?" always
#: answers none. Only ever visible when something queries for absence.
NullableJsonType: Final = JSON(none_as_null=True).with_variant(
    JSONB(none_as_null=True), "postgresql"
)

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
        datetime: UtcDateTime(),
    }


class AccountRow(Base):
    """Billing and quota root. Owns many workspaces (ADR 0011)."""

    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="free")
    quota: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    __table_args__ = (UniqueConstraint("slug", name="uq_accounts_slug"),)


class ActorRow(Base):
    """Whoever -- or whatever -- took an action.

    Account-scoped, not global: the same person working for two agencies is two
    actors, because preference learning is per-actor per-account.
    """

    __tablename__ = "actors"

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="human")
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("account_id", "key", name="uq_actors_account_id_key"),
        Index("ix_actors_account_id", "account_id"),
    )


class MembershipRow(Base):
    """Links an actor to an account with a role."""

    __tablename__ = "memberships"

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("actors.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="editor")
    created_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        UniqueConstraint("account_id", "actor_id", name="uq_memberships_account_id_actor_id"),
        Index("ix_memberships_actor_id", "actor_id"),
    )


class WorkspaceRow(Base):
    """Tenant root."""

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    pack_key: Mapped[str] = mapped_column(String(64), nullable=False, default="ai-news")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    languages: Mapped[list[str]] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("slug", name="uq_workspaces_slug"),
        Index("ix_workspaces_account_id", "account_id"),
    )


class SignalRow(Base):
    """Append-only feedback log (ADR 0012).

    No update path exists by design: a correction is a new row. This table is
    both the training set for every learning loop and the audit trail, and
    neither is trustworthy if rows can be rewritten.

    ``account_id`` is denormalised rather than joined through the workspace,
    because quota, metering and cross-brand analytics all query by account and
    this is the highest-volume table in the system.
    """

    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)
    workspace_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)

    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)

    signal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    polarity: Mapped[str] = mapped_column(String(16), nullable=False, default="neutral")

    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    context: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    run_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        # "Everything that happened to this subject" -- the dominant read.
        Index(
            "ix_signals_workspace_id_subject_type_subject_id",
            "workspace_id",
            "subject_type",
            "subject_id",
        ),
        # Learning queries pull one signal type across a time range.
        Index(
            "ix_signals_workspace_id_signal_type_occurred_at",
            "workspace_id",
            "signal_type",
            "occurred_at",
        ),
        Index("ix_signals_account_id_occurred_at", "account_id", "occurred_at"),
        Index("ix_signals_run_id", "run_id"),
    )


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


class StoryRow(Base):
    """A canonical story, assembled from one or more candidates.

    ``url_key`` and ``title_key`` are the clustering keys, stored rather than
    recomputed so matching is an indexed lookup instead of a scan.
    """

    __tablename__ = "stories"

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    canonical_headline: Mapped[str] = mapped_column(String(1024), nullable=False)
    canonical_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    url_key: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    title_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    topics: Mapped[list[str]] = mapped_column(nullable=False, default=list)
    entities: Mapped[list[str]] = mapped_column(nullable=False, default=list)
    enrichment: Mapped[dict[str, Any] | None] = mapped_column(NullableJsonType, nullable=True)
    selected_at: Mapped[datetime | None] = mapped_column(nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    __table_args__ = (
        # Clustering looks up by key inside one workspace; neither key is
        # unique, because two genuinely different stories can share a landing
        # page and the stage decides, not the database.
        Index("ix_stories_workspace_id_url_key", "workspace_id", "url_key"),
        Index("ix_stories_workspace_id_title_key", "workspace_id", "title_key"),
        Index("ix_stories_workspace_id_last_seen_at", "workspace_id", "last_seen_at"),
        Index("ix_stories_workspace_id_status", "workspace_id", "status"),
        # "What did we select today?" runs on every pass from here to
        # publication, so it is an indexed column rather than a JSON probe.
        Index("ix_stories_workspace_id_selected_at", "workspace_id", "selected_at"),
    )


class StoryCandidateRow(Base):
    """One story as mentioned by one document.

    Derived data, unlike ``source_documents``: re-parsing a document replaces
    its candidates. The unique constraint on ``(document_id, ordinal)`` is what
    makes a repeated parse a no-op rather than a duplicate.
    """

    __tablename__ = "story_candidates"

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    #: Null until resolution clusters this mention into a canonical story.
    story_id: Mapped[str | None] = mapped_column(
        String(ID_LENGTH), ForeignKey("stories.id", ondelete="SET NULL"), nullable=True
    )
    headline: Mapped[str] = mapped_column(String(1024), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    raw_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    ordinal: Mapped[int] = mapped_column(nullable=False, default=0)
    extracted_at: Mapped[datetime] = mapped_column(nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_story_candidates_document_id_ordinal"),
        Index("ix_story_candidates_workspace_id_document_id", "workspace_id", "document_id"),
        Index("ix_story_candidates_workspace_id_source_id", "workspace_id", "source_id"),
        Index("ix_story_candidates_extracted_at", "extracted_at"),
        # Drives both "what is left to cluster?" and "what is in this story?".
        Index("ix_story_candidates_workspace_id_story_id", "workspace_id", "story_id"),
    )


class EvidenceRow(Base):
    """One claim bound to a source that supports it.

    Unique on ``(story_id, kind, source_url)``: evidence is reconciled on every
    run, and the constraint is what makes a repeated run a no-op rather than a
    growing pile of identical rows.
    """

    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    story_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("stories.id", ondelete="CASCADE"), nullable=False
    )
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_title: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "story_id", "kind", "source_url", name="uq_evidence_story_id_kind_source_url"
        ),
        Index("ix_evidence_workspace_id_story_id", "workspace_id", "story_id"),
        Index("ix_evidence_workspace_id_kind", "workspace_id", "kind"),
    )


class EditorialScoreRow(Base):
    """Why a story ranked where it did, on one run.

    Append-only history rather than one row per story: "what did we think last
    Tuesday, and with which weights?" is the question that makes refitting the
    weights possible, and an upsert would erase it.
    """

    __tablename__ = "editorial_scores"

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    story_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("stories.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)
    total: Mapped[float] = mapped_column(Float, nullable=False)
    components: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    raw: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    scored_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        # One score per story per run. Re-running a run is a bug; re-scoring
        # deliberately produces a new run id and therefore a new row.
        UniqueConstraint("story_id", "run_id", name="uq_editorial_scores_story_id_run_id"),
        Index("ix_editorial_scores_workspace_id_story_id", "workspace_id", "story_id"),
        Index("ix_editorial_scores_workspace_id_total", "workspace_id", "total"),
        Index("ix_editorial_scores_scored_at", "scored_at"),
    )


class ContentAssetRow(Base):
    """One generated artifact for one story, product, channel and language.

    Carries identity and workflow state only -- the text lives in the revision
    chain (ADR 0013). ``current_revision_id`` is deliberately **not** a foreign
    key: assets and revisions reference each other, and a real constraint in
    both directions cannot be satisfied by any insert order.
    """

    __tablename__ = "content_assets"

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    story_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("stories.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)
    product_key: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    brand_profile_key: Mapped[str] = mapped_column(String(64), nullable=False)
    current_revision_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    variant: Mapped[str] = mapped_column(String(16), nullable=False, default="a")
    evidence_ids: Mapped[list[str]] = mapped_column(nullable=False, default=list)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    __table_args__ = (
        # One asset per addressable slot, which is what makes regenerating a
        # single channel safe: the slot is overwritten with a new revision
        # rather than duplicated.
        UniqueConstraint(
            "story_id",
            "product_key",
            "channel_key",
            "language",
            "variant",
            name="uq_content_assets_slot",
        ),
        Index("ix_content_assets_workspace_id_state", "workspace_id", "state"),
        Index("ix_content_assets_workspace_id_story_id", "workspace_id", "story_id"),
        Index("ix_content_assets_workspace_id_created_at", "workspace_id", "created_at"),
    )


class ContentRevisionRow(Base):
    """One version of an asset's text. Append-only.

    The generated-versus-published difference is the training signal the whole
    revision chain exists to preserve, so nothing here is ever updated in place.
    """

    __tablename__ = "content_revisions"

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("content_assets.id", ondelete="CASCADE"), nullable=False
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)
    parent_revision_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Columns rather than a JSON blob, matching the run ledger: these are
    # summed and grouped, and arithmetic over JSON is a query nobody writes.
    #
    # ``server_default`` as well as ``default``: the migration needed one to
    # add NOT NULL columns to a table that already held rows, and declaring it
    # here too keeps the model and the database in agreement -- otherwise every
    # autogenerate run reports a difference that is not really there.
    input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    cache_read_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    cache_write_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    cost_usd: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "asset_id", "revision_number", name="uq_content_revisions_asset_id_revision_number"
        ),
        Index("ix_content_revisions_workspace_id_asset_id", "workspace_id", "asset_id"),
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
