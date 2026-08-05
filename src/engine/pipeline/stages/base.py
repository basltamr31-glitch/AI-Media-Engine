"""The stage contract.

Every phase of the roadmap adds stages, never a new pipeline. Ingest, extract,
resolve, editorial, generate, review, distribute and measure all implement this
one interface, which is why the orchestrator, ledger and cost accounting are
written once in Phase 0 and never rewritten.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from engine.core.domain.base import DomainModel
from engine.core.domain.ledger import TokenUsage
from engine.pipeline.context import RunContext


class StageResult(DomainModel):
    """What a stage produced, plus what it cost.

    Cost and item counts are part of the contract rather than something stages
    optionally log, so the run ledger is complete by construction.
    """

    items_in: int = Field(default=0, ge=0)
    items_out: int = Field(default=0, ge=0)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost_usd: float = Field(default=0.0, ge=0.0)
    skipped: bool = False
    skip_reason: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def skip(cls, reason: str) -> StageResult:
        """A stage that legitimately had nothing to do."""
        return cls(skipped=True, skip_reason=reason)


@runtime_checkable
class Stage(Protocol):
    """One unit of pipeline work."""

    @property
    def name(self) -> str:
        """Stable identifier used in the ledger and in logs."""
        ...

    def run(self, context: RunContext) -> StageResult:
        """Execute against ``context``.

        Implementations must be idempotent where possible: a re-run over the
        same inputs should not duplicate work. This is what makes partial
        failure recoverable instead of requiring a manual cleanup.
        """
        ...


class StageError(RuntimeError):
    """Raised when a stage fails in a way that should abort the run."""

    def __init__(self, stage_name: str, message: str) -> None:
        self.stage_name = stage_name
        super().__init__(f"stage {stage_name!r} failed: {message}")
