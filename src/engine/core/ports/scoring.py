"""Signal computation port.

One computer per ranking signal, each answering a single question about a story
in its own units. The weighted sum happens elsewhere; a computer never knows
its own weight, so re-weighting is a config change rather than a code change.

Separating them this way is what makes ranking *explainable*: every number in
an :class:`~engine.core.domain.story.EditorialScore` traces back to exactly one
computer, and a component that looks wrong points at one testable function.

Computers are pure and cheap by design. Ranking runs over every story on every
run, so anything that costs a network call or a model invocation per story does
not belong here -- that is what the strategy means by never scaling linearly in
LLM cost where an algorithm will do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from engine.core.domain.policy import EditorialPolicy
from engine.core.domain.source import Source
from engine.core.domain.story import Story, StoryCandidate


@dataclass(frozen=True)
class SignalContext:
    """Everything a computer may look at, and nothing it may not.

    Deliberately narrow: a computer that could reach the database could also
    become slow, order-dependent, or untestable. Everything here is already
    loaded by the stage, once, for the whole batch.
    """

    #: The mentions this story was assembled from, in document order.
    mentions: list[StoryCandidate]
    #: Sources by id, for trust weights and names.
    sources: dict[str, Source]
    #: The workspace's editorial rules -- priority and blocked topics.
    policy: EditorialPolicy
    #: Run time, injected rather than read from the clock so a re-score of
    #: history produces the same numbers it did the first time.
    now: datetime
    #: The signal's declared parameters, from the pack. The same values the
    #: normaliser receives, because a ceiling is a property of the signal
    #: rather than of one half of its arithmetic.
    parameters: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SignalComputer(Protocol):
    """Measures one ranking signal for one story."""

    @property
    def key(self) -> str:
        """Ranking-signal key this computer answers for, e.g. ``recency``."""
        ...

    def compute(self, story: Story, context: SignalContext) -> float | None:
        """Measure the signal, in the computer's own units.

        Returns:
            A raw value for the normaliser, or None when the signal cannot be
            measured for this story. None is not zero: a missing measurement is
            excluded from the score and reported, whereas zero is a real
            measurement that drags the total down.
        """
        ...
