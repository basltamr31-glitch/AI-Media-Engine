"""Brand voice, channel rules, generated assets, publications and performance.

Two decisions are encoded here and both are monetisation-critical:

1. ``BrandProfile`` and ``ChannelSpec`` are **data, not prompts**. Onboarding a
   customer becomes inserting rows, not editing Python. This is the single
   change that makes multi-tenant generation possible.
2. ``ContentAsset`` is addressable and versioned per story/channel/language, so
   one channel can be regenerated without touching the rest, variants can be
   A/B tested, and performance can be attributed back to a specific version.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from engine.core.domain.base import DomainModel, ImmutableModel, TenantScoped, utcnow
from engine.core.domain.enums import (
    Channel,
    ContentKind,
    ContentState,
    Language,
    can_transition,
)
from engine.core.domain.ids import IdPrefix, new_id


class BrandProfile(TenantScoped):
    """A voice definition: who is speaking, to whom, and how.

    "It doesn't sound like me" is the top reason people abandon AI content
    tools, so voice is a first-class, per-workspace, per-language entity rather
    than a paragraph buried in a prompt template.
    """

    id: str = Field(default_factory=lambda: new_id(IdPrefix.BRAND))
    key: str = Field(description="Stable lookup key, unique per workspace.")
    name: str
    language: Language
    audience: str = Field(description="Who we are writing for, in plain words.")
    tone_rules: list[str] = Field(default_factory=list)
    style_rules: list[str] = Field(default_factory=list)
    forbidden_topics: list[str] = Field(default_factory=list)
    preferred_terms: dict[str, str] = Field(
        default_factory=dict,
        description="Term substitutions. Critical for Arabic technical vocabulary.",
    )
    signature_phrases: list[str] = Field(default_factory=list)
    example_posts: list[str] = Field(
        default_factory=list,
        description="Few-shot exemplars. The strongest single lever on voice fidelity.",
    )
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ChannelSpec(DomainModel):
    """Hard formatting constraints for one distribution channel.

    Not tenant-scoped: these are properties of the platform, shared by everyone.
    """

    key: Channel
    name: str
    max_characters: int = Field(gt=0)
    supports_hashtags: bool = True
    max_hashtags: int = Field(default=0, ge=0)
    supports_links: bool = True
    preferred_kinds: list[ContentKind] = Field(default_factory=list)
    format_notes: list[str] = Field(
        default_factory=list,
        description="Channel conventions passed to generation as explicit rules.",
    )

    @model_validator(mode="after")
    def _validate_hashtags(self) -> ChannelSpec:
        if self.supports_hashtags and self.max_hashtags == 0:
            msg = f"channel {self.key} supports hashtags but max_hashtags is 0"
            raise ValueError(msg)
        return self


class RevisionOrigin(StrEnum):
    """How a revision came to exist."""

    GENERATED = "generated"  # written by a model
    EDITED = "edited"  # changed by a human
    RESTORED = "restored"  # an earlier revision reinstated


class ContentRevision(ImmutableModel):
    """One version of a content asset's text. Immutable (ADR 0013).

    Editing appends a revision; it never overwrites one. The reason is narrow
    and commercial: the difference between the ``generated`` revision and the
    ``edited`` one that actually got published is a labelled preference pair in
    the customer's own voice, produced as a by-product of work they were doing
    anyway. Overwriting ``body`` would discard it silently and permanently.

    Restoring an earlier version appends a ``restored`` revision rather than
    moving a pointer backwards, so the history reads as what actually happened.
    """

    id: str = Field(default_factory=lambda: new_id(IdPrefix.REVISION))
    workspace_id: str
    asset_id: str
    revision_number: int = Field(ge=1, description="Monotonic within one asset.")
    body: str
    origin: RevisionOrigin
    actor_id: str = Field(
        description="Who produced it. Model output is attributed to the system actor."
    )
    parent_revision_id: str | None = Field(
        default=None,
        description="What this was derived from. None for the first revision.",
    )
    prompt_version: str | None = Field(
        default=None,
        description=(
            "Which prompt produced a generated revision. Without it, a style "
            "regression after a prompt change cannot be attributed."
        ),
    )
    created_at: datetime = Field(default_factory=utcnow)
    meta: dict[str, Any] = Field(default_factory=dict)


class ContentAsset(TenantScoped):
    """One generated artifact for one story, channel, language and kind.

    Holds identity and workflow state. The **text lives in the revision chain**,
    not here, which is what preserves the draft-versus-published difference.
    """

    id: str = Field(default_factory=lambda: new_id(IdPrefix.ASSET))
    story_id: str
    run_id: str
    kind: ContentKind
    channel: Channel | None = Field(
        default=None,
        description="None for channel-agnostic assets such as image prompts.",
    )
    language: Language
    brand_profile_key: str
    current_revision_id: str | None = Field(
        default=None,
        description=(
            "Pointer to the live revision, so reading current content stays a "
            "single indexed lookup rather than a scan of the chain."
        ),
    )
    state: ContentState = ContentState.DRAFT
    variant: str = Field(
        default="a",
        description="Variant label for A/B testing.",
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="Provenance: which evidence backs the claims in this asset.",
    )
    review_notes: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    meta: dict[str, Any] = Field(default_factory=dict)

    def next_revision(
        self,
        body: str,
        *,
        origin: RevisionOrigin,
        actor_id: str,
        previous: ContentRevision | None = None,
        prompt_version: str | None = None,
    ) -> ContentRevision:
        """Build the next revision in the chain.

        Returns the revision without persisting it or advancing
        ``current_revision_id``: the caller owns the transaction, and the
        pointer must only move once the revision is durable.
        """
        return ContentRevision(
            workspace_id=self.workspace_id,
            asset_id=self.id,
            revision_number=1 if previous is None else previous.revision_number + 1,
            body=body,
            origin=origin,
            actor_id=actor_id,
            parent_revision_id=None if previous is None else previous.id,
            prompt_version=prompt_version,
        )

    def transition_to(self, target: ContentState, *, notes: str | None = None) -> None:
        """Move through the review workflow, rejecting illegal transitions.

        Raises:
            ValueError: If the transition is not permitted.
        """
        if not can_transition(self.state, target):
            msg = f"illegal content transition: {self.state} -> {target}"
            raise ValueError(msg)
        self.state = target
        if notes is not None:
            self.review_notes = notes
        self.updated_at = utcnow()


class Publication(ImmutableModel):
    """Record of an asset going live. Immutable: it either happened or it did not."""

    id: str = Field(default_factory=lambda: new_id(IdPrefix.PUBLICATION))
    workspace_id: str
    asset_id: str
    channel: Channel
    external_url: str | None = None
    external_id: str | None = None
    published_at: datetime = Field(default_factory=utcnow)
    published_by: str = Field(
        default="manual",
        description="'manual' in V1. Auto-publishing is deliberately deferred.",
    )


class PerformanceMetric(ImmutableModel):
    """One engagement observation for a publication.

    This closes the loop. Every competitor generates blind; capturing outcomes --
    even typed in by hand at first -- is what turns the ranking model into a
    proprietary asset instead of a prompt anyone can copy.
    """

    id: str = Field(default_factory=lambda: new_id(IdPrefix.METRIC))
    workspace_id: str
    publication_id: str
    metric: str = Field(description="e.g. impressions, likes, saves, shares, clicks.")
    value: float
    captured_at: datetime = Field(default_factory=utcnow)
    collected_by: str = Field(default="manual")

    @field_validator("metric")
    @classmethod
    def _normalise_metric(cls, value: str) -> str:
        normalised = value.strip().lower().replace(" ", "_")
        if not normalised:
            msg = "metric name must not be empty"
            raise ValueError(msg)
        return normalised
