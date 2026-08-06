"""The signal log -- every human decision and every measured outcome.

ADR 0012. One append-only table rather than one table per feedback type.

This is the asset a competitor cannot buy, scrape or prompt their way to: which
stories were rejected, which drafts were edited, which posts performed, which
sources break news first. It is also the only data in the system that is
**not backfillable** -- you cannot reconstruct which headline a human would have
chosen last March. That is why capture starts now rather than in the phase that
consumes it.

Signals are immutable. A correction is a new signal, never an edit. A log that
can be rewritten is neither a training set nor an audit trail.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from pydantic import Field, model_validator

from engine.core.domain.base import ImmutableModel, utcnow
from engine.core.domain.enums import SignalPolarity, SubjectType
from engine.core.domain.ids import IdPrefix, new_id

# --------------------------------------------------------------------------
# Signal type keys.
#
# Deliberately module constants, not an enum: `signal_type` is validated against
# the active domain pack (ADR 0015), so a vertical can define its own without a
# migration. These are the engine's built-in vocabulary -- the ones the core
# pipeline emits itself.
# --------------------------------------------------------------------------

# Editorial judgement -- feeds ranking-weight learning
SIGNAL_STORY_SELECTED: Final = "story.selected"
SIGNAL_STORY_REJECTED: Final = "story.rejected"
SIGNAL_STORY_SCORED: Final = "story.scored"

# Resolution quality -- feeds similarity-threshold tuning
SIGNAL_STORIES_MERGED: Final = "resolution.merged"
SIGNAL_STORIES_LINKED: Final = "resolution.linked"
SIGNAL_MERGE_CORRECTED: Final = "resolution.corrected"

# Content lifecycle -- feeds style learning (the strongest moat asset)
SIGNAL_CONTENT_GENERATED: Final = "content.generated"
SIGNAL_CONTENT_EDITED: Final = "content.edited"
SIGNAL_CONTENT_APPROVED: Final = "content.approved"
SIGNAL_CONTENT_REJECTED: Final = "content.rejected"
SIGNAL_CONTENT_PUBLISHED: Final = "content.published"

# Outcomes -- feeds headline, timing and topic learning
SIGNAL_ENGAGEMENT: Final = "publication.engagement"

# Source quality -- feeds trust_weight auto-tuning
SIGNAL_SOURCE_YIELD: Final = "source.yield"

BUILT_IN_SIGNAL_TYPES: Final[frozenset[str]] = frozenset(
    {
        SIGNAL_STORY_SELECTED,
        SIGNAL_STORY_REJECTED,
        SIGNAL_STORY_SCORED,
        SIGNAL_STORIES_MERGED,
        SIGNAL_STORIES_LINKED,
        SIGNAL_MERGE_CORRECTED,
        SIGNAL_CONTENT_GENERATED,
        SIGNAL_CONTENT_EDITED,
        SIGNAL_CONTENT_APPROVED,
        SIGNAL_CONTENT_REJECTED,
        SIGNAL_CONTENT_PUBLISHED,
        SIGNAL_ENGAGEMENT,
        SIGNAL_SOURCE_YIELD,
    }
)


class Signal(ImmutableModel):
    """One thing that happened, and what it tells us."""

    id: str = Field(default_factory=lambda: new_id(IdPrefix.SIGNAL))
    account_id: str
    workspace_id: str

    actor_id: str = Field(
        description="Who acted. Automated actions use the account's system actor, never null."
    )

    subject_type: SubjectType
    subject_id: str

    signal_type: str = Field(
        description="Open vocabulary. Validated against the active domain pack, not an enum."
    )
    polarity: SignalPolarity = Field(
        default=SignalPolarity.NEUTRAL,
        description=(
            "Whether this is evidence for or against the subject. Lets a learner "
            "consume unfamiliar signal types without knowing their semantics."
        ),
    )

    value: float | None = Field(
        default=None,
        description="Numeric outcome: engagement count, score, edit distance.",
    )
    label: str | None = Field(
        default=None,
        description="Categorical outcome: a rejection reason, a variant key.",
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Whatever the emitter knew. Kept open so new signals need no migration.",
    )

    run_id: str | None = Field(
        default=None,
        description="Pipeline run that emitted this, when machine-generated.",
    )

    occurred_at: datetime = Field(
        default_factory=utcnow,
        description="When the event happened.",
    )
    recorded_at: datetime = Field(
        default_factory=utcnow,
        description=(
            "When we learned about it. Distinct from occurred_at because engagement "
            "is pulled hours later; collapsing them would corrupt every timing analysis."
        ),
    )

    @model_validator(mode="after")
    def _validate_payload(self) -> Signal:
        if self.value is None and self.label is None and not self.context:
            msg = (
                f"signal {self.signal_type!r} carries no value, label or context; "
                "a signal with no payload records nothing learnable"
            )
            raise ValueError(msg)
        if self.recorded_at < self.occurred_at:
            msg = "recorded_at cannot precede occurred_at"
            raise ValueError(msg)
        return self

    @property
    def latency_seconds(self) -> float:
        """How long after the event we learned about it."""
        return (self.recorded_at - self.occurred_at).total_seconds()
