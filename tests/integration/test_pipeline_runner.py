"""Pipeline orchestration: ledger persistence, ordering, cost ceiling, failures.

Every future stage inherits this machinery, so it is worth testing thoroughly
now while it can be exercised in isolation from business logic.
"""

from __future__ import annotations

import pytest

from engine.adapters.clock import FixedClock
from engine.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWork
from engine.core.domain.enums import RunStatus, StageStatus
from engine.core.domain.ledger import TokenUsage
from engine.core.domain.workspace import Workspace
from engine.pipeline.context import RunContext
from engine.pipeline.runner import CostLimitExceededError, PipelineRunner
from engine.pipeline.stages.base import Stage, StageResult
from engine.pipeline.stages.noop import NoOpStage
from engine.settings import Settings

pytestmark = pytest.mark.integration


class RecordingStage:
    """Test double that records invocation order."""

    def __init__(self, name: str, order: list[str], *, cost: float = 0.0) -> None:
        self._name = name
        self._order = order
        self._cost = cost

    @property
    def name(self) -> str:
        return self._name

    def run(self, context: RunContext) -> StageResult:
        self._order.append(self._name)
        return StageResult(
            items_in=1,
            items_out=2,
            cost_usd=self._cost,
            usage=TokenUsage(input_tokens=100, output_tokens=50),
        )


class FailingStage:
    @property
    def name(self) -> str:
        return "explodes"

    def run(self, context: RunContext) -> StageResult:
        msg = "stage blew up"
        raise RuntimeError(msg)


@pytest.fixture
def runner(uow: SqlAlchemyUnitOfWork, clock: FixedClock, settings: Settings) -> PipelineRunner:
    return PipelineRunner(uow=uow, clock=clock, settings=settings)


def test_empty_pipeline_still_records_a_run(
    runner: PipelineRunner, uow: SqlAlchemyUnitOfWork, persisted_workspace: Workspace
):
    """Phase 0's end-to-end proof: the spine works before any stage exists."""
    ledger = runner.run(workspace_id=persisted_workspace.id, pipeline="daily_content", stages=[])

    assert ledger.status is RunStatus.SUCCEEDED
    with uow as active:
        stored = active.runs.get(persisted_workspace.id, ledger.id)
    assert stored is not None
    assert stored.status is RunStatus.SUCCEEDED
    assert stored.finished_at is not None


def test_stages_run_in_order_and_are_recorded(
    runner: PipelineRunner, uow: SqlAlchemyUnitOfWork, persisted_workspace: Workspace
):
    order: list[str] = []
    stages = [
        RecordingStage("ingest", order),
        RecordingStage("extract", order),
        RecordingStage("resolve", order),
    ]

    ledger = runner.run(
        workspace_id=persisted_workspace.id, pipeline="daily_content", stages=stages
    )

    assert order == ["ingest", "extract", "resolve"]
    with uow as active:
        records = active.runs.list_stages(persisted_workspace.id, ledger.id)
    assert [record.stage for record in records] == ["ingest", "extract", "resolve"]
    assert [record.sequence for record in records] == [0, 1, 2]
    assert all(record.status is StageStatus.SUCCEEDED for record in records)


def test_usage_and_cost_aggregate_across_stages(
    runner: PipelineRunner, persisted_workspace: Workspace
):
    order: list[str] = []
    stages = [
        RecordingStage("a", order, cost=0.01),
        RecordingStage("b", order, cost=0.02),
    ]

    ledger = runner.run(
        workspace_id=persisted_workspace.id, pipeline="daily_content", stages=stages
    )

    assert ledger.cost_usd == pytest.approx(0.03)
    assert ledger.usage.input_tokens == 200
    assert ledger.usage.output_tokens == 100


def test_cost_ceiling_aborts_the_run(
    uow: SqlAlchemyUnitOfWork,
    clock: FixedClock,
    settings: Settings,
    persisted_workspace: Workspace,
):
    """A runaway loop is a financial incident; the ceiling must actually stop it."""
    capped = settings.model_copy(update={"max_cost_usd_per_run": 0.05})
    runner = PipelineRunner(uow=uow, clock=clock, settings=capped)
    order: list[str] = []

    with pytest.raises(CostLimitExceededError):
        runner.run(
            workspace_id=persisted_workspace.id,
            pipeline="daily_content",
            stages=[RecordingStage("expensive", order, cost=0.10)],
        )

    with uow as active:
        runs = active.runs.list_recent(persisted_workspace.id)
    assert runs[0].status is RunStatus.FAILED


def test_failure_is_persisted_with_the_error(
    runner: PipelineRunner, uow: SqlAlchemyUnitOfWork, persisted_workspace: Workspace
):
    order: list[str] = []
    stages: list[Stage] = [
        RecordingStage("ok", order),
        FailingStage(),
        RecordingStage("never", order),
    ]

    with pytest.raises(RuntimeError, match="stage blew up"):
        runner.run(workspace_id=persisted_workspace.id, pipeline="daily_content", stages=stages)

    assert order == ["ok"]  # the stage after the failure must not run

    with uow as active:
        runs = active.runs.list_recent(persisted_workspace.id)
        records = active.runs.list_stages(persisted_workspace.id, runs[0].id)

    assert runs[0].status is RunStatus.FAILED
    assert "stage blew up" in (runs[0].error or "")
    failed = [record for record in records if record.status is StageStatus.FAILED]
    assert len(failed) == 1
    assert failed[0].stage == "explodes"


def test_skipped_stage_is_marked_skipped(
    runner: PipelineRunner, uow: SqlAlchemyUnitOfWork, persisted_workspace: Workspace
):
    class SkippingStage:
        @property
        def name(self) -> str:
            return "nothing_to_do"

        def run(self, context: RunContext) -> StageResult:
            return StageResult.skip("no new documents")

    ledger = runner.run(
        workspace_id=persisted_workspace.id,
        pipeline="daily_content",
        stages=[SkippingStage()],
    )

    with uow as active:
        records = active.runs.list_stages(persisted_workspace.id, ledger.id)
    assert records[0].status is StageStatus.SKIPPED
    assert records[0].meta["skip_reason"] == "no new documents"


def test_dry_run_flag_reaches_the_stage(runner: PipelineRunner, persisted_workspace: Workspace):
    ledger = runner.run(
        workspace_id=persisted_workspace.id,
        pipeline="daily_content",
        stages=[NoOpStage("smoke")],
        dry_run=True,
    )
    assert ledger.meta["dry_run"] is True
