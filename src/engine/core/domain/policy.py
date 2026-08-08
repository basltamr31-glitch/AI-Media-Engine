"""Editorial policy -- the ranking rules, expressed as data.

ADR 0016. Ranking is the product's real intellectual property, so it must be
inspectable, tunable without a code change, and eventually fitted against
measured engagement.

The previous shape -- five named float fields on a model with ``extra="forbid"``
-- made all three impossible. Adding one ranking signal meant editing the domain
model, editing a validator, migrating every workspace file and redeploying. And
the signals a *vertical* needs are not the signals AI news needs: cybersecurity
ranks on CVSS severity, pharma on trial phase, finance on market cap. None of
those could exist.

So weights are an **open map**, validated against the signals the active domain
pack declares. A new signal is a pack entry, not a release.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from engine.core.domain.base import DomainModel

#: Floating-point sums never land exactly on 1.0, and a profile that is off by
#: 0.3 is a typo rather than arithmetic noise.
WEIGHT_SUM_TOLERANCE = 1e-6


class ScoringProfile(DomainModel):
    """Relative weight of each ranking signal. Open map; must sum to 1.0.

    Keys are validated against the pack's declared signals by
    :meth:`validate_against`, not here -- the model alone cannot know which
    signals exist, and a profile is often constructed before its pack is loaded.
    """

    weights: dict[str, float] = Field(default_factory=dict)

    @field_validator("weights")
    @classmethod
    def _validate_weights(cls, value: dict[str, float]) -> dict[str, float]:
        for key, weight in value.items():
            if not 0.0 <= weight <= 1.0:
                msg = f"weight for {key!r} must be between 0 and 1, got {weight}"
                raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> ScoringProfile:
        if not self.weights:
            return self
        total = sum(self.weights.values())
        if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
            msg = (
                f"scoring weights must sum to 1.0, got {total:.6f} "
                f"across {len(self.weights)} signal(s)"
            )
            raise ValueError(msg)
        return self

    @property
    def is_empty(self) -> bool:
        """True when no weights are set, meaning "inherit the pack default"."""
        return not self.weights

    @property
    def signal_keys(self) -> set[str]:
        return set(self.weights)

    def weight_of(self, signal_key: str) -> float:
        """Weight for one signal. Zero for a signal this profile ignores."""
        return self.weights.get(signal_key, 0.0)

    def validate_against(self, known_signal_keys: set[str], *, pack_key: str = "") -> None:
        """Check every weighted signal actually exists.

        Called once at load time. A misspelled key would otherwise contribute
        zero silently, and a ranking that is quietly wrong is worse than one
        that fails loudly.

        Raises:
            ValueError: If any weighted signal is not declared by the pack.
        """
        unknown = sorted(self.signal_keys - known_signal_keys)
        if unknown:
            available = ", ".join(sorted(known_signal_keys)) or "<none>"
            where = f" in pack {pack_key!r}" if pack_key else ""
            msg = (
                f"scoring profile references unknown ranking signal(s) {unknown}{where}; "
                f"available: {available}"
            )
            raise ValueError(msg)


class EditorialPolicy(DomainModel):
    """Per-workspace editorial rules governing selection."""

    scoring: ScoringProfile = Field(
        default_factory=ScoringProfile,
        description=(
            "Signal weights. Left empty, the workspace inherits the active pack's default profile."
        ),
    )
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
