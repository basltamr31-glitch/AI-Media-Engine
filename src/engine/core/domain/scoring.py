"""Normalisers -- turning a raw measurement into a comparable 0..1 score.

A signal computer answers a question in its own units: how many sources, how
many hours old, how many points. Those cannot be added together, so each
:class:`~engine.core.domain.pack.RankingSignal` declares a normaliser that maps
its units onto the same scale.

Kept as pure functions in the domain because this is the arithmetic the whole
ranking rests on: it has to be arguable from a test case, not from a running
database, and it must never differ between the scoring stage and whatever
re-scores history later.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Final

from engine.core.domain.pack import Normaliser

#: Used when a signal declares a ceiling-based normaliser without saying what
#: the ceiling is. One means "any positive value is full marks", which is
#: visibly wrong rather than quietly wrong.
_DEFAULT_CEILING: Final = 1.0

#: Hours after which a decayed value is worth half. A day, matching the
#: editorial reality that yesterday's launch is not today's story.
_DEFAULT_HALF_LIFE_HOURS: Final = 24.0


def clamp(value: float) -> float:
    """Force a value into 0..1. Every normaliser ends here."""
    return max(0.0, min(1.0, value))


def normalise(
    value: float,
    normaliser: Normaliser,
    parameters: Mapping[str, Any] | None = None,
) -> float:
    """Map a raw measurement onto 0..1 according to ``normaliser``.

    Args:
        value: The computer's raw output, in its own units.
        normaliser: How to interpret those units.
        parameters: The signal's declared parameters -- ``ceiling`` for the
            linear and logarithmic forms, ``half_life_hours`` for decay.

    Returns:
        A score in 0..1, always. A signal that could exceed 1 would silently
        outvote its own weight.
    """
    params = parameters or {}

    if normaliser is Normaliser.PASSTHROUGH:
        # The computer already answered on a 0..1 scale. Clamped anyway: a
        # computer that returns 1.7 is a bug, and one that quietly counts
        # double is worse than one that is capped.
        return clamp(value)

    if normaliser is Normaliser.LINEAR:
        ceiling = _positive(params.get("ceiling"), _DEFAULT_CEILING)
        return clamp(value / ceiling)

    if normaliser is Normaliser.LOGARITHMIC:
        # For counts whose first few are worth far more than their hundredth:
        # 10 upvotes versus 100 matters, 10,000 versus 10,100 does not.
        ceiling = _positive(params.get("ceiling"), _DEFAULT_CEILING)
        if value <= 0:
            return 0.0
        return clamp(math.log1p(value) / math.log1p(ceiling))

    if normaliser is Normaliser.DECAY:
        # Exponential half-life over an age in hours.
        half_life = _positive(params.get("half_life_hours"), _DEFAULT_HALF_LIFE_HOURS)
        if value <= 0:
            return 1.0  # brand new, or a clock skew; either way, not stale
        return clamp(0.5 ** (value / half_life))

    # Unreachable while Normaliser stays closed, and mypy proves it. Kept as a
    # guard so that adding a member without a branch here fails loudly instead
    # of quietly scoring every story on that signal as zero.
    msg = f"unsupported normaliser {normaliser!r}"  # type: ignore[unreachable]
    raise ValueError(msg)


def _positive(value: Any, fallback: float) -> float:
    """A usable positive number, or the fallback.

    Pack parameters are data written by hand; a zero ceiling would divide by
    zero and a negative half-life would invert the decay.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback
