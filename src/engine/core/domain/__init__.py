"""Domain entities, value objects and enumerations.

This package is pure: no database, no HTTP, no LLM, no framework. That purity is
what allows the same domain to be driven by a CLI today and an HTTP API later
without modification.
"""

from __future__ import annotations

from engine.core.domain.base import DomainModel, ImmutableModel, TenantScoped, utcnow
from engine.core.domain.content import (
    BrandProfile,
    ChannelSpec,
    ContentAsset,
    PerformanceMetric,
    Publication,
)
from engine.core.domain.enums import (
    CONTENT_STATE_TRANSITIONS,
    Channel,
    ContentKind,
    ContentState,
    EvidenceKind,
    Language,
    RunStatus,
    SourceKind,
    StageStatus,
    StoryStatus,
    can_transition,
)
from engine.core.domain.ids import IdPrefix, is_valid_id, new_id, timestamp_of, ulid
from engine.core.domain.ledger import RunLedger, StageRecord, TokenUsage
from engine.core.domain.policy import EditorialPolicy, ScoringWeights
from engine.core.domain.source import Source, SourceDocument, compute_content_hash
from engine.core.domain.story import (
    MAX_QUOTE_WORDS,
    EditorialScore,
    EnrichedSource,
    Evidence,
    Story,
    StoryCandidate,
    StoryWithScore,
)
from engine.core.domain.workspace import Workspace

__all__ = [
    "CONTENT_STATE_TRANSITIONS",
    "MAX_QUOTE_WORDS",
    "BrandProfile",
    "Channel",
    "ChannelSpec",
    "ContentAsset",
    "ContentKind",
    "ContentState",
    "DomainModel",
    "EditorialPolicy",
    "EditorialScore",
    "EnrichedSource",
    "Evidence",
    "EvidenceKind",
    "IdPrefix",
    "ImmutableModel",
    "Language",
    "PerformanceMetric",
    "Publication",
    "RunLedger",
    "RunStatus",
    "ScoringWeights",
    "Source",
    "SourceDocument",
    "SourceKind",
    "StageRecord",
    "StageStatus",
    "Story",
    "StoryCandidate",
    "StoryStatus",
    "StoryWithScore",
    "TenantScoped",
    "TokenUsage",
    "Workspace",
    "can_transition",
    "compute_content_hash",
    "is_valid_id",
    "new_id",
    "timestamp_of",
    "ulid",
    "utcnow",
]
