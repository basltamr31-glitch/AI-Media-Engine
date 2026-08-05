"""Sources and the raw documents fetched from them.

A ``Source`` is configuration (what to fetch, how much to trust it); a
``SourceDocument`` is an immutable record of one fetch. Keeping raw content
means every downstream decision can be replayed and audited without re-fetching
-- essential for debugging an LLM pipeline and for defending a factual claim.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from engine.core.domain.base import ImmutableModel, TenantScoped, utcnow
from engine.core.domain.enums import SourceKind
from engine.core.domain.ids import IdPrefix, new_id


class Source(TenantScoped):
    """A configured input feed.

    ``kind`` is the transport (email/rss/api/web) and ``parser_key`` selects the
    format-specific parser. That split is what keeps TLDR from leaking into the
    architecture: swapping or adding a newsletter never touches pipeline code.
    """

    id: str = Field(default_factory=lambda: new_id(IdPrefix.SOURCE))
    kind: SourceKind
    name: str
    identifier: str = Field(
        description="What to fetch: feed URL, sender address, or API endpoint.",
    )
    parser_key: str | None = Field(
        default=None,
        description="Registry key of the parser for this source's format.",
    )
    trust_weight: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Editorial weight. Feeds ranking: a story corroborated by two "
            "high-trust sources outranks one shouted by a low-trust source."
        ),
    )
    is_active: bool = True
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Parser/transport options. Data, so new sources need no code.",
    )
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @field_validator("identifier")
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        if not value.strip():
            msg = "identifier must not be empty"
            raise ValueError(msg)
        return value.strip()


def compute_content_hash(*parts: str) -> str:
    """Stable content fingerprint used for idempotent ingestion.

    Re-running the pipeline must never double-ingest the same document, and
    external IDs are not reliably stable across providers, so we hash the
    content itself.
    """
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.strip().encode("utf-8"))
        digest.update(b"\x1f")  # unit separator prevents field-boundary collisions
    return digest.hexdigest()


class SourceDocument(ImmutableModel):
    """One raw artifact fetched from a source. Append-only, never edited."""

    id: str = Field(default_factory=lambda: new_id(IdPrefix.DOCUMENT))
    workspace_id: str
    source_id: str
    external_id: str | None = Field(
        default=None,
        description="Provider-side identifier (message ID, GUID) when available.",
    )
    content_hash: str = Field(description="Deduplication key. See compute_content_hash.")
    title: str | None = None
    author: str | None = None
    url: str | None = None
    content_type: str = "text/plain"
    raw_content: str = Field(description="Verbatim payload; the audit trail's foundation.")
    published_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=utcnow)
    meta: dict[str, Any] = Field(default_factory=dict)
