"""A stage that does nothing.

Not filler. It lets Phase 0 execute a real end-to-end run -- context created,
ledger written, stages recorded, cost enforced, transaction committed -- and
prove the orchestration works before any business logic exists to obscure a
failure. It is also the reference implementation of the stage contract and the
fixture the runner's tests are built on.
"""

from __future__ import annotations

from engine.pipeline.context import RunContext
from engine.pipeline.stages.base import StageResult


class NoOpStage:
    """Records an execution and returns an empty result."""

    def __init__(self, name: str = "noop", *, items: int = 0) -> None:
        self._name = name
        self._items = items

    @property
    def name(self) -> str:
        return self._name

    def run(self, context: RunContext) -> StageResult:
        return StageResult(
            items_in=self._items,
            items_out=self._items,
            meta={"dry_run": context.dry_run},
        )
