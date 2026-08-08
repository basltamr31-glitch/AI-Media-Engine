"""Domain entities, value objects and enumerations.

This package is pure: no database, no HTTP, no LLM, no framework. That purity is
what allows the same domain to be driven by a CLI today and an HTTP API later
without modification.
"""

from __future__ import annotations

from engine.core.domain.base import DomainModel, ImmutableModel, TenantScoped, utcnow
from engine.core.domain.content import (
    BrandProfile,
    ContentAsset,
    ContentRevision,
    PerformanceMetric,
    Publication,
    RevisionOrigin,
)
from engine.core.domain.enums import (
    CONTENT_STATE_TRANSITIONS,
    ContentState,
    EvidenceKind,
    RunStatus,
    SourceKind,
    StageStatus,
    StoryStatus,
    can_transition,
)
from engine.core.domain.ids import IdPrefix, is_valid_id, new_id, timestamp_of, ulid
from engine.core.domain.language import ARABIC, ENGLISH, LanguageTag, base_language
from engine.core.domain.ledger import RunLedger, StageRecord, TokenUsage
from engine.core.domain.pack import (
    ChannelDef,
    DomainPack,
    Normaliser,
    ProductDef,
    ProductKind,
    RankingSignal,
    TaxonomyNode,
)
from engine.core.domain.policy import EditorialPolicy, ScoringProfile
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
    "ARABIC",
    "CONTENT_STATE_TRANSITIONS",
    "ENGLISH",
    "MAX_QUOTE_WORDS",
    "BrandProfile",
    "ChannelDef",
    "ContentAsset",
    "ContentRevision",
    "ContentState",
    "DomainModel",
    "DomainPack",
    "EditorialPolicy",
    "EditorialScore",
    "EnrichedSource",
    "Evidence",
    "EvidenceKind",
    "IdPrefix",
    "ImmutableModel",
    "LanguageTag",
    "Normaliser",
    "PerformanceMetric",
    "ProductDef",
    "ProductKind",
    "Publication",
    "RankingSignal",
    "RevisionOrigin",
    "RunLedger",
    "RunStatus",
    "ScoringProfile",
    "Source",
    "SourceDocument",
    "SourceKind",
    "StageRecord",
    "StageStatus",
    "Story",
    "StoryCandidate",
    "StoryStatus",
    "StoryWithScore",
    "TaxonomyNode",
    "TenantScoped",
    "TokenUsage",
    "Workspace",
    "base_language",
    "can_transition",
    "compute_content_hash",
    "is_valid_id",
    "new_id",
    "timestamp_of",
    "ulid",
    "utcnow",
]
