"""Editorial policy -- the ranking rules, expressed as data.

Ranking is the product's real intellectual property, so it must be inspectable
and tunable without a code change, and eventually fitted against measured
engagement. Hard-coded weights would make all three impossible.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from engine.core.domain.base import DomainModel


class ScoringWeights(DomainModel):
    """Relative weight of each editorial signal. Must sum to 1.0."""

    corroboration: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="How many independent sources reported it. The strongest honest signal.",
    )
    source_authority: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Aggregate trust_weight of the reporting sources.",
    )
    recency: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Freshness decay. Yesterday's launch is not today's story.",
    )
    prominence: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Where sources placed it. Editors rank by ordering.",
    )
    audience_fit: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Match to workspace topics. Later: fitted on real engagement.",
    )

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> ScoringWeights:
        total = (
            self.corroboration
            + self.source_authority
            + self.recency
            + self.prominence
            + self.audience_fit
        )
        if abs(total - 1.0) > 1e-6:
            msg = f"scoring weights must sum to 1.0, got {total:.6f}"
            raise ValueError(msg)
        return self

    def as_dict(self) -> dict[str, float]:
        return self.model_dump()


class EditorialPolicy(DomainModel):
    """Per-workspace editorial rules governing selection."""

    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    max_stories_per_run: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Deliberately small. Selectivity is the product; volume is not.",
    )
    minimum_score: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description="Below this a story is never produced, even if nothing better exists.",
    )
    recency_half_life_hours: float = Field(default=24.0, gt=0.0)
    require_primary_source: bool = Field(
        default=True,
        description=(
            "Enforces 'newsletters are tips, not text'. Turning this off would "
            "make the pipeline a republisher, which is both a legal risk and a "
            "commodity product."
        ),
    )
    priority_topics: list[str] = Field(default_factory=list)
    blocked_topics: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
