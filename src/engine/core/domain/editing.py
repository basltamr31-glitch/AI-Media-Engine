"""Measuring what a human changed about a draft.

The revision chain keeps the model's text and the human's text side by side
(ADR 0013). This module turns that pair into numbers, because the pair alone
is not queryable: "which channel needs the most rewriting?" and "is the prompt
getting better?" are questions about aggregates, and aggregating requires a
measurement taken at the moment of the edit.

Every measure here is **algorithmic** -- a ratio, a difference of lengths, a
sequence match. Nothing calls a model, for the same reason the quality checks
do not: a measurement that costs a model call per edit is one that gets turned
off in the month it starts to matter.

The distinction that matters commercially is *how much* was changed, not
merely *that* it was. A post published untouched and a post rewritten from
scratch are both "edited"; only the second says the generator failed.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Final

from pydantic import Field

from engine.core.domain.base import ImmutableModel

#: Below this similarity, the human did not adjust the draft -- they replaced
#: it. Chosen as a reporting boundary only: nothing branches on it, so a
#: borderline case is mislabelled in a summary rather than mishandled.
REWRITE_THRESHOLD: Final = 0.5


class EditDelta(ImmutableModel):
    """How far a human moved a draft, in numbers a query can aggregate."""

    characters_before: int = Field(ge=0)
    characters_after: int = Field(ge=0)
    similarity: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "1.0 when the text is unchanged, 0.0 when it shares nothing. "
            "A character-level sequence ratio, so a reordered sentence scores "
            "as a smaller change than a replaced one -- which is what an "
            "editor would say happened."
        ),
    )

    @property
    def characters_changed(self) -> int:
        """Net growth or shrinkage. Negative when the human cut."""
        return self.characters_after - self.characters_before

    @property
    def is_rewrite(self) -> bool:
        """Whether this replaced the draft rather than adjusted it."""
        return self.similarity < REWRITE_THRESHOLD

    @property
    def is_unchanged(self) -> bool:
        """An edit that changed nothing. Worth recording, not worth learning from."""
        return self.similarity >= 1.0

    def as_context(self) -> dict[str, float | int | bool]:
        """The flat shape the signal log stores.

        Flat rather than nested because the whole point is being able to
        average one of these across ten thousand rows without unpacking a
        structure first.
        """
        return {
            "characters_before": self.characters_before,
            "characters_after": self.characters_after,
            "characters_changed": self.characters_changed,
            "similarity": round(self.similarity, 4),
            "is_rewrite": self.is_rewrite,
        }


def measure_edit(before: str, after: str) -> EditDelta:
    """Compare a draft with what a human replaced it with.

    ``SequenceMatcher`` compares characters rather than words deliberately.
    Arabic is the language this engine exists to serve well, and it marks
    agreement, definiteness and possession with affixes -- so a genuinely small
    correction changes the inside of a word, which a word-level ratio would
    score as a whole-token replacement and overstate.
    """
    return EditDelta(
        characters_before=len(before),
        characters_after=len(after),
        # Two empty strings ratio to 1.0 rather than dividing by zero, so the
        # degenerate case needs no guard of its own.
        similarity=SequenceMatcher(None, before, after).ratio(),
    )
