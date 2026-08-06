"""Domain enumerations and the content review state machine.

Enums are ``StrEnum`` so they serialise to readable strings in JSON, SQL and
logs. Values are persisted, so they are part of the data contract: add freely,
rename never.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class Language(StrEnum):
    """Output languages. Arabic is the strategic wedge, not an afterthought.

    .. deprecated:: Phase 1.5
        Superseded by validated BCP-47 language tags (ADR 0015). A closed enum
        cannot express a German finance customer without a migration. Retained
        until milestone 1.5.5 migrates the call sites.
    """

    AR = "ar"
    EN = "en"


class PlanTier(StrEnum):
    """Commercial tier of an account.

    Engine-controlled and genuinely closed -- tiers select engine behaviour
    (quota enforcement, feature gates), unlike channels or products which
    enumerate a market we do not control. See ADR 0015 for the distinction.
    """

    FREE = "free"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    AGENCY = "agency"
    ENTERPRISE = "enterprise"


class ActorKind(StrEnum):
    """What kind of thing took an action."""

    HUMAN = "human"
    SYSTEM = "system"  # the pipeline acting unattended
    SCHEDULER = "scheduler"
    API_CLIENT = "api_client"


class MembershipRole(StrEnum):
    """An actor's role within an account."""

    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class Channel(StrEnum):
    """Distribution channels enabled for V1.

    Facebook, TikTok and the blog are deliberately absent: breadth before
    quality is how content products become mediocre everywhere.
    """

    INSTAGRAM = "instagram"
    X = "x"
    LINKEDIN = "linkedin"


class SourceKind(StrEnum):
    """How a source is fetched -- transport, not brand.

    The engine has an *email* ingester, not a "TLDR reader". Adding a newsletter
    is a config row plus a parser, never a new subsystem.
    """

    EMAIL = "email"
    RSS = "rss"
    API = "api"
    WEB = "web"


class StoryStatus(StrEnum):
    """Lifecycle of a story as it moves through the pipeline."""

    CANDIDATE = "candidate"  # extracted from one document, not yet clustered
    RESOLVED = "resolved"  # clustered across sources into a canonical story
    ENRICHED = "enriched"  # primary source fetched; safe to write about
    SELECTED = "selected"  # chosen by editorial for production
    REJECTED = "rejected"  # scored but not selected
    PUBLISHED = "published"


class EvidenceKind(StrEnum):
    """Provenance tier of a piece of evidence.

    Encodes the editorial rule that newsletters are *tips*, not *text*: an
    AGGREGATOR reference may justify attention, but only a PRIMARY source may
    justify a factual claim.
    """

    PRIMARY = "primary"  # the paper, the announcement, the official blog
    SECONDARY = "secondary"  # reputable reporting on the primary source
    AGGREGATOR = "aggregator"  # newsletter or feed that surfaced it


class ContentKind(StrEnum):
    """What a generated asset is."""

    SOCIAL_POST = "social_post"
    CAPTION = "caption"
    HASHTAGS = "hashtags"
    REEL_SCRIPT = "reel_script"
    VOICEOVER = "voiceover"
    IMAGE_PROMPT = "image_prompt"


class ContentState(StrEnum):
    """Review workflow state. The human gate is a feature, not an obstacle."""

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class SubjectType(StrEnum):
    """What a signal is about.

    Engine-controlled: these are the entities the pipeline itself manipulates,
    so the set closes with the engine rather than with the market.
    """

    STORY = "story"
    STORY_CANDIDATE = "story_candidate"
    CONTENT_ASSET = "content_asset"
    PUBLICATION = "publication"
    SOURCE = "source"
    DOCUMENT = "document"


class SignalPolarity(StrEnum):
    """Whether a signal is evidence for or against its subject.

    Lets a learner consume a signal type it has never seen -- including one
    defined by a third-party domain pack -- without knowing its semantics.
    """

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


# The review workflow, expressed as data rather than scattered `if` statements.
# Nothing reaches a channel without passing through APPROVED, and PUBLISHED is
# terminal apart from archival -- an audit trail requirement once real clients
# are involved.
CONTENT_STATE_TRANSITIONS: Final[dict[ContentState, frozenset[ContentState]]] = {
    ContentState.DRAFT: frozenset({ContentState.IN_REVIEW, ContentState.ARCHIVED}),
    ContentState.IN_REVIEW: frozenset(
        {ContentState.APPROVED, ContentState.REJECTED, ContentState.DRAFT}
    ),
    ContentState.APPROVED: frozenset(
        {ContentState.PUBLISHED, ContentState.DRAFT, ContentState.ARCHIVED}
    ),
    ContentState.REJECTED: frozenset({ContentState.DRAFT, ContentState.ARCHIVED}),
    ContentState.PUBLISHED: frozenset({ContentState.ARCHIVED}),
    ContentState.ARCHIVED: frozenset(),
}


def can_transition(current: ContentState, target: ContentState) -> bool:
    """Return True if ``current -> target`` is a legal review transition."""
    return target in CONTENT_STATE_TRANSITIONS[current]
