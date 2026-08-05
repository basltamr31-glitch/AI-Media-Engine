"""Stories, evidence and editorial scores -- the core intellectual property.

The value of this system is not generation; it is knowing *which* stories matter
and being able to prove *why* each claim is true. Those two jobs live here.

Flow: many ``StoryCandidate`` mentions across sources are clustered into one
canonical ``Story``, which is then enriched from its primary source and scored
by an explainable ``EditorialScore``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from pydantic import Field, field_validator

from engine.core.domain.base import DomainModel, ImmutableModel, TenantScoped, utcnow
from engine.core.domain.enums import EvidenceKind, StoryStatus
from engine.core.domain.ids import IdPrefix, new_id

#: Hard ceiling on quoted words from any third-party source. Encodes the
#: fair-use posture directly in the type system so it cannot be forgotten under
#: deadline pressure.
MAX_QUOTE_WORDS: Final = 25


class StoryCandidate(TenantScoped):
    """One story as mentioned by one source document.

    Deliberately *not* the canonical story: the same launch appears in five
    newsletters with five headlines, and collapsing them is a separate,
    reviewable step.
    """

    id: str = Field(default_factory=lambda: new_id(IdPrefix.CANDIDATE))
    document_id: str
    source_id: str
    headline: str
    summary: str | None = None
    primary_url: str | None = Field(
        default=None,
        description="Where the source pointed. The lead we follow for enrichment.",
    )
    raw_excerpt: str | None = Field(
        default=None,
        description="Verbatim slice of the document, retained for traceability only.",
    )
    ordinal: int = Field(
        default=0,
        ge=0,
        description="Position within the document. Newsletters lead with what they rank highest.",
    )
    extracted_at: datetime = Field(default_factory=utcnow)
    meta: dict[str, Any] = Field(default_factory=dict)


class EnrichedSource(ImmutableModel):
    """Content fetched from a story's primary source.

    This is the load-bearing piece of the "newsletters are tips, not text" rule:
    we learn *what happened* from the newsletter and *what to say* from the
    original announcement, paper or repository.
    """

    url: str
    title: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    text: str = Field(description="Extracted main content of the primary source.")
    fetched_at: datetime = Field(default_factory=utcnow)
    extraction_method: str = Field(default="unknown", description="Which extractor produced this.")


class Evidence(ImmutableModel):
    """A single claim bound to the source that supports it.

    Together these form the provenance ledger: a legal defence, a trust feature
    users can see, and the debugging tool that answers "where did this sentence
    come from?".
    """

    id: str = Field(default_factory=lambda: new_id(IdPrefix.EVIDENCE))
    workspace_id: str
    story_id: str
    claim: str = Field(description="The assertion, in our own words.")
    kind: EvidenceKind
    source_url: str
    source_title: str | None = None
    quote: str | None = Field(
        default=None,
        description=f"Optional verbatim support, hard-capped at {MAX_QUOTE_WORDS} words.",
    )
    captured_at: datetime = Field(default_factory=utcnow)

    @field_validator("quote")
    @classmethod
    def _enforce_quote_limit(cls, value: str | None) -> str | None:
        if value is None:
            return None
        word_count = len(value.split())
        if word_count > MAX_QUOTE_WORDS:
            msg = (
                f"quote exceeds the {MAX_QUOTE_WORDS}-word fair-use limit "
                f"({word_count} words). Paraphrase and cite instead."
            )
            raise ValueError(msg)
        return value


class Story(TenantScoped):
    """A canonical, deduplicated story assembled from one or more candidates."""

    id: str = Field(default_factory=lambda: new_id(IdPrefix.STORY))
    canonical_headline: str
    canonical_summary: str | None = None
    primary_url: str | None = None
    status: StoryStatus = StoryStatus.CANDIDATE
    candidate_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(
        default_factory=list,
        description="Distinct sources reporting this. Corroboration is a ranking signal.",
    )
    topics: list[str] = Field(default_factory=list)
    entities: list[str] = Field(
        default_factory=list,
        description="Organisations, models, people. Powers dedupe and interest matching.",
    )
    enrichment: EnrichedSource | None = None
    first_seen_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def source_count(self) -> int:
        """Number of distinct sources that reported this story."""
        return len(set(self.source_ids))

    @property
    def is_publishable(self) -> bool:
        """True only once a primary source has been fetched.

        The editorial contract: we do not write about a story we have only read
        about second-hand.
        """
        return self.enrichment is not None


class EditorialScore(ImmutableModel):
    """Why a story ranked where it did.

    ``components`` is kept broken out rather than collapsed into a single number
    so ranking stays explainable, tunable, and -- once performance data arrives --
    trainable against real engagement.
    """

    id: str = Field(default_factory=lambda: new_id(IdPrefix.SCORE))
    workspace_id: str
    story_id: str
    run_id: str
    total: float = Field(ge=0.0, le=1.0)
    components: dict[str, float] = Field(
        default_factory=dict,
        description="Per-signal contributions, e.g. corroboration/recency/authority.",
    )
    rationale: str | None = Field(
        default=None,
        description="Human-readable justification surfaced in the review queue.",
    )
    scored_at: datetime = Field(default_factory=utcnow)


class StoryWithScore(DomainModel):
    """Convenience pairing used by editorial selection and the review UI."""

    story: Story
    score: EditorialScore
