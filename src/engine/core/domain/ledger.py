"""Run ledger -- observability and unit economics.

Every pipeline execution and every stage within it is recorded: what went in,
what came out, how long it took, how many tokens it burned and what it cost.

This exists in Phase 0, before a single paid API call, deliberately. You cannot
price a product whose unit economics you do not measure, and cost instrumentation
retrofitted after launch is always incomplete.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from engine.core.domain.base import DomainModel, TenantScoped, utcnow
from engine.core.domain.enums import RunStatus, StageStatus
from engine.core.domain.ids import IdPrefix, new_id


class TokenUsage(DomainModel):
    """Token accounting for one stage or run."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(
        default=0,
        ge=0,
        description="Prompt-cache hits, billed at ~10% of input. Tracked to prove caching works.",
    )
    cache_write_tokens: int = Field(default=0, ge=0)

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


class StageRecord(TenantScoped):
    """Execution record for a single pipeline stage."""

    id: str = Field(default_factory=lambda: new_id(IdPrefix.STAGE))
    run_id: str
    stage: str
    sequence: int = Field(ge=0)
    status: StageStatus = StageStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    items_in: int = Field(default=0, ge=0)
    items_out: int = Field(default=0, ge=0)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost_usd: float = Field(default=0.0, ge=0.0)
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


class RunLedger(TenantScoped):
    """Execution record for a whole pipeline run.

    Persisted per run so any execution can be inspected, compared or replayed
    long after the process has exited.
    """

    id: str = Field(default_factory=lambda: new_id(IdPrefix.RUN))
    pipeline: str
    status: RunStatus = RunStatus.PENDING
    trigger: str = Field(default="cli", description="cli | schedule | api")
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost_usd: float = Field(default=0.0, ge=0.0)
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def is_terminal(self) -> bool:
        return self.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
