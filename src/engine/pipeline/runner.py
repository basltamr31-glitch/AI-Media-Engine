"""Pipeline orchestration.

Runs an ordered list of stages, recording each one in the ledger as it goes and
enforcing the per-run cost ceiling. Written once here so that every future
stage inherits observability, cost accounting and failure handling for free.

Stage records are committed incrementally rather than at the end: a run that
crashes must leave behind evidence of how far it got.
"""

from __future__ import annotations

from collections.abc import Sequence

from engine.core.domain.enums import RunStatus, StageStatus
from engine.core.domain.ledger import RunLedger, StageRecord
from engine.core.ports.clock import Clock
from engine.core.ports.repositories import UnitOfWork
from engine.observability.logging import (
    bind_run_context,
    clear_run_context,
    get_logger,
)
from engine.pipeline.context import RunContext
from engine.pipeline.stages.base import Stage, StageResult
from engine.settings import Settings

logger = get_logger(__name__)


class CostLimitExceededError(RuntimeError):
    """Raised when a run exceeds its configured spend ceiling.

    A runaway agentic loop is a financial incident, not just a bug. The ceiling
    exists from the first commit rather than after the first surprising invoice.
    """


class PipelineRunner:
    """Executes stages in order and records the outcome."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        clock: Clock,
        settings: Settings,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._settings = settings

    def run(
        self,
        *,
        workspace_id: str,
        pipeline: str,
        stages: Sequence[Stage],
        trigger: str = "cli",
        dry_run: bool = False,
    ) -> RunLedger:
        """Execute ``stages`` and return the completed ledger entry."""
        started_at = self._clock.now()
        ledger = RunLedger(
            workspace_id=workspace_id,
            pipeline=pipeline,
            status=RunStatus.RUNNING,
            trigger=trigger,
            started_at=started_at,
            meta={"dry_run": dry_run, "stage_count": len(stages)},
        )

        bind_run_context(run_id=ledger.id, workspace_id=workspace_id, pipeline=pipeline)
        logger.info("run.started", stage_count=len(stages), dry_run=dry_run)

        with self._uow as uow:
            uow.runs.add(ledger)
            uow.commit()

        context = RunContext(
            run_id=ledger.id,
            workspace_id=workspace_id,
            pipeline=pipeline,
            settings=self._settings,
            clock=self._clock,
            uow=self._uow,
            started_at=started_at,
            dry_run=dry_run,
        )

        try:
            for sequence, stage in enumerate(stages):
                result = self._run_stage(ledger, context, stage, sequence)
                ledger.usage = ledger.usage + result.usage
                ledger.cost_usd += result.cost_usd
                self._enforce_cost_ceiling(ledger)
            ledger.status = RunStatus.SUCCEEDED
        except Exception as exc:
            ledger.status = RunStatus.FAILED
            ledger.error = f"{type(exc).__name__}: {exc}"
            logger.exception("run.failed", error=ledger.error)
            raise
        finally:
            ledger.finished_at = self._clock.now()
            with self._uow as uow:
                uow.runs.update(ledger)
                uow.commit()
            logger.info(
                "run.finished",
                status=ledger.status.value,
                duration_seconds=ledger.duration_seconds,
                cost_usd=round(ledger.cost_usd, 6),
            )
            clear_run_context()

        return ledger

    def _run_stage(
        self,
        ledger: RunLedger,
        context: RunContext,
        stage: Stage,
        sequence: int,
    ) -> StageResult:
        record = StageRecord(
            workspace_id=ledger.workspace_id,
            run_id=ledger.id,
            stage=stage.name,
            sequence=sequence,
            status=StageStatus.RUNNING,
            started_at=self._clock.now(),
        )
        with self._uow as uow:
            uow.runs.add_stage(record)
            uow.commit()

        stage_logger = logger.bind(stage=stage.name, sequence=sequence)
        stage_logger.info("stage.started")

        try:
            result = stage.run(context)
        except Exception as exc:
            record.status = StageStatus.FAILED
            record.error = f"{type(exc).__name__}: {exc}"
            record.finished_at = self._clock.now()
            with self._uow as uow:
                uow.runs.update_stage(record)
                uow.commit()
            stage_logger.error("stage.failed", error=record.error)
            raise

        record.status = StageStatus.SKIPPED if result.skipped else StageStatus.SUCCEEDED
        record.finished_at = self._clock.now()
        record.items_in = result.items_in
        record.items_out = result.items_out
        record.usage = result.usage
        record.cost_usd = result.cost_usd
        record.meta = result.meta | (
            {"skip_reason": result.skip_reason} if result.skip_reason else {}
        )

        with self._uow as uow:
            uow.runs.update_stage(record)
            uow.commit()

        stage_logger.info(
            "stage.finished",
            status=record.status.value,
            items_in=record.items_in,
            items_out=record.items_out,
            duration_seconds=record.duration_seconds,
        )
        return result

    def _enforce_cost_ceiling(self, ledger: RunLedger) -> None:
        limit = self._settings.max_cost_usd_per_run
        if ledger.cost_usd > limit:
            msg = f"run cost ${ledger.cost_usd:.4f} exceeded the ${limit:.2f} ceiling"
            raise CostLimitExceededError(msg)
