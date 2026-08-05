"""Execution context passed to every pipeline stage.

Stages receive their dependencies rather than constructing them. That is what
makes a stage testable in isolation with fakes, and what will allow the same
stage code to run in-process today and inside a queue worker later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from engine.core.ports.clock import Clock
from engine.core.ports.repositories import UnitOfWork
from engine.settings import Settings


@dataclass(slots=True)
class RunContext:
    """Everything a stage needs, and nothing it does not."""

    run_id: str
    workspace_id: str
    pipeline: str
    settings: Settings
    clock: Clock
    uow: UnitOfWork
    started_at: datetime
    dry_run: bool = False
    #: Scratch space for passing values between stages without widening the
    #: stage signature. Kept explicit so cross-stage coupling stays visible.
    state: dict[str, Any] = field(default_factory=dict)

    def elapsed_seconds(self) -> float:
        return (self.clock.now() - self.started_at).total_seconds()
