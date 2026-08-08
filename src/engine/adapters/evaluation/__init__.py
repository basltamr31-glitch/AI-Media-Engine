"""Evaluation: making quality regressions visible.

The checks themselves live in :mod:`engine.core.domain.evaluation` -- they are
pure algorithms over text and belong with the domain. What lives here is the
machinery around them: reading the golden set off disk, and driving generation
through the real prompt path so the suite measures the product rather than
itself.
"""

from engine.adapters.evaluation.golden import GoldenSet, GoldenSetError
from engine.adapters.evaluation.runner import CaseOutcome, EvalReport, EvalRunner

__all__ = [
    "CaseOutcome",
    "EvalReport",
    "EvalRunner",
    "GoldenSet",
    "GoldenSetError",
]
