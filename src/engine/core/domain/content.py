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


class ContentAsset(TenantScoped):
    """One generated artifact for one story, channel, language and kind."""

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
    body: str
    state: ContentState = ContentState.DRAFT
    version: int = Field(default=1, ge=1)
    variant: str = Field(
        default="a",
        description="Variant label for A/B testing within a version.",
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="Provenance: which evidence backs the claims in this asset.",
    )
    review_notes: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    meta: dict[str, Any] = Field(default_factory=dict)

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
