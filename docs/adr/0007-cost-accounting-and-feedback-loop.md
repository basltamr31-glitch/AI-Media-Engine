# ADR 0007: Instrument cost and outcomes before either exists

- **Status:** Accepted
- **Date:** Phase 0

## Context

Phase 0 makes no LLM calls and publishes nothing. There is no cost to measure and
no engagement to record. Building instrumentation for both now looks premature.

Two arguments say otherwise.

**Cost.** The product will be priced. Pricing requires knowing the cost of
producing one day of content for one workspace — by stage, by model, by channel.
Instrumentation added after launch is always partial, because the interesting
question is usually about the past. An agentic pipeline can also loop: a runaway
run is a financial incident, and the ceiling that prevents it has to exist before
the first paid call, not after the first surprising invoice.

**Outcomes.** Every competing content tool generates blind — nobody knows what
performed. Closing the loop from published content back into ranking and
generation produces a proprietary dataset that compounds and cannot be copied by
cloning prompts. That dataset only exists if collection starts before it is
useful. Data not captured in month one is not available in month twelve.

## Decision

Build both loops in Phase 0, populated by hand at first.

**Cost accounting**

- `TokenUsage` records input, output and cache read/write tokens.
- `StageRecord` carries usage, cost, item counts and duration per stage.
- `RunLedger` aggregates per run, with `trigger` distinguishing cli / schedule / api.
- `StageResult` includes usage and cost *in the contract*, so the ledger is
  complete by construction rather than by stages remembering to log.
- `PipelineRunner` enforces `max_cost_usd_per_run` and aborts on breach.
- `LLMResult` carries usage alongside the value, so cost attributes to a specific
  story, channel and workspace.

Cache tokens are tracked separately because prompt caching is a primary
gross-margin lever, and "is the cache actually being hit?" must be answerable
with data.

**Feedback loop**

- `Publication` records an asset going live: channel, URL, timestamp.
- `PerformanceMetric` records engagement observations, with `collected_by`
  defaulting to `"manual"` — typed in from platform dashboards until API access
  is worth building.
- `EditorialScore.components` keeps per-signal contributions rather than a single
  number, so weights can later be fitted against measured engagement.

## Alternatives considered

**Add cost tracking when LLM calls arrive (Phase 3).** Nearly free to do now,
and doing it now means the runner, ledger and stage contract are designed around
it rather than patched. Retrofitting would touch every stage.

**Add performance capture when publishing is automated (Phase 5+).** Rejected:
the value is the accumulated history. Starting collection in month six discards
the first six months of the most valuable dataset the company will own.

**Use an external observability platform.** Useful later for traces and
dashboards; it does not replace a queryable, per-tenant ledger owned by the
product and used by the ranking model.

## Consequences

**Positive**

- Unit economics are visible from the first run, so pricing rests on data.
- Runaway spend is bounded by construction.
- The feedback dataset begins accumulating before it is needed.
- Ranking is fittable rather than hand-tuned forever.

**Negative**

- Fields sit empty through Phases 0–2, which looks like over-engineering until
  Phase 3.
- Manual metric entry is tedious. Accepted deliberately: tedium is a good filter
  for whether the data is worth automating.
