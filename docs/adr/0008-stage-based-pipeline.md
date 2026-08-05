# ADR 0008: A stage contract and a run ledger, before any stage exists

- **Status:** Accepted
- **Date:** Phase 0

## Context

The roadmap adds capability in phases: ingestion, then extraction and resolution,
then editorial and generation, then rendering, then distribution and measurement.

The natural way to build that is a script that grows: fetch, then parse, then
score, each phase appending to a `main()` function. It works and it is fast to
write. It also produces a system where nothing can be re-run in isolation, a
failure at step six discards the work of steps one to five, and observability and
cost accounting are bolted on per step, differently each time.

For an LLM pipeline this is especially costly. Debugging "why did Tuesday's run
choose those three stories?" requires inspecting intermediate state that a
straight-line script has already discarded, and re-running generation after a
prompt change should not mean re-fetching and re-scoring everything.

## Decision

Define one narrow stage contract in Phase 0 and orchestrate around it.

```python
class Stage(Protocol):
    @property
    def name(self) -> str: ...
    def run(self, context: RunContext) -> StageResult: ...
```

- `RunContext` carries injected dependencies — settings, clock, unit of work,
  run and workspace identity, dry-run flag — so stages construct nothing.
- `StageResult` carries item counts, token usage, cost and skip status, making
  the ledger complete by construction.
- `PipelineRunner` executes stages in order, records each to the ledger
  incrementally, enforces the cost ceiling, and marks the run failed with the
  error on exception.
- `NoOpStage` implements the contract, letting Phase 0 execute a real end-to-end
  run and prove the spine works before business logic can obscure a defect.

Stage records are committed as they complete, not at the end: a crashed run must
leave evidence of how far it got.

Every subsequent phase adds stages. None of them modifies the runner.

## Alternatives considered

**A script that grows per phase.** Fastest to Phase 1. Rejected: no replay, no
partial re-runs, no uniform observability, and every phase re-solves error
handling.

**An external orchestrator (Airflow, Prefect, Dagster).** Real replay and
scheduling. Rejected as premature: a daemon and a large dependency for a pipeline
that runs once a day on one machine. The `Stage` protocol is deliberately
compatible with wrapping in such a tool later — one adapter, no stage rewrites.

**Event-driven stages over a queue.** Where this goes if throughput demands it.
Rejected now: distributed debugging cost with no distributed workload.

## Consequences

**Positive**

- Observability, cost accounting and failure handling are written once and
  inherited by every future stage.
- Stages are unit-testable with fakes, since dependencies arrive via context.
- Every run is inspectable after the fact via `engine pipeline stages <run-id>`.
- Moving to a queue or external scheduler later is an adapter, not a rewrite.

**Negative**

- More structure than Phase 0 strictly needs; the value only materialises from
  Phase 1 onward.
- `RunContext.state` allows implicit inter-stage coupling. Kept explicit and
  visible so the coupling is at least auditable; if it grows, stages should pass
  data through persisted entities instead.
