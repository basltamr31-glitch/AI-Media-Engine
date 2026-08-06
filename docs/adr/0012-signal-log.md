# ADR 0012: One append-only signal log, not seven feedback tables

- **Status:** Accepted
- **Date:** Phase 1.5

## Context

The product's defensibility comes from data a competitor cannot buy, scrape or
prompt their way to: which stories a human rejected, which drafts were edited,
which posts performed, which sources break news first. `STRATEGY.md` lists seven
distinct learning loops that depend on this.

Two problems with the current design.

**First, none of it is captured.** `PerformanceMetric` exists as a model with no
collection path. Rejection reasons are not stored at all. The original roadmap
put this work in Phase 5.

**Data is not backfillable.** Cost accounting can be added late and still be
correct going forward. Feedback cannot: you cannot reconstruct which headline a
human would have chosen last March. Every month without capture is a month
permanently missing from the training set.

**Second, the obvious design is wrong.** The natural instinct is one table per
feedback type — `rejections`, `edits`, `approvals`, `engagements`, `clicks`.
That is seven migrations, seven repositories, seven query paths, and an eighth
whenever a new loop appears. Since the whole point is that we do not yet know
which signals will matter, a schema that requires a migration per signal type is
a schema that discourages collecting signals.

## Decision

**One append-only table.** Every human decision and every measured outcome is a
row:

```
Signal
  id, account_id, workspace_id
  actor_id            who (or the `system` actor)
  subject_type        story | content_asset | source | publication | candidate
  subject_id
  signal_type         rejected | approved | edited | published | impression …
  value               float | null      numeric outcomes
  label               str   | null      categorical outcomes
  context             JSON               whatever the emitter knows
  occurred_at         when it happened (not when it was recorded)
  recorded_at
```

`signal_type` is an open string validated against the domain pack, not an enum
(ADR 0015). A new loop needs a pack entry, never a migration.

**Append-only.** No updates, no deletes. A corrected signal is a new signal.
This is what makes the log trustworthy as a training set and as an audit trail —
a table that can be edited is not evidence.

**Emission is a port.** `SignalEmitter` in `core/ports`, so stages record
feedback without importing persistence, and tests assert on emissions with a
recording fake.

**`occurred_at` is separate from `recorded_at`.** Engagement metrics are pulled
hours after the fact. Collapsing the two would make every timing analysis wrong,
and timing is one of the seven loops.

## Consequences

**Good**

- Capture starts in Phase 2 instead of Phase 5 — roughly a year of additional
  data by the time the learning phase runs.
- New signal types cost a config line.
- One query surface. "Everything that happened to this story" is one `WHERE`.
- The append-only property makes the log usable as an audit trail in regulated
  verticals without a second mechanism.

**Costs, accepted**

- A generic table is less type-safe than seven specific ones. Mitigated by typed
  emission helpers per signal family, so callers do not hand-build rows.
- It will become the largest table by row count. Partitioning by
  `occurred_at` is the standard answer and is a Postgres-era concern.
- `context` as JSON means some analysis needs JSON operators. Acceptable: the
  alternative is a schema that must anticipate every future signal.

## Alternatives considered

**A table per feedback type.** Type-safe and query-friendly, but each new loop is
a migration, which in practice means loops do not get added. Rejected because the
explicit goal is to make collecting a new signal nearly free.

**An external analytics tool (Segment, PostHog).** Excellent for product
analytics, wrong here: this data is the training input to our own ranking and
style models, not just dashboards. It must live where the pipeline can join it to
stories and content, and it must be ours.

**Deriving signals from state transitions instead of recording them.** Tempting —
`ContentState` already moves `DRAFT → APPROVED`. Rejected because state records
*where something ended up*, not *what happened or why*. A rejection reason, an
edit distance and an engagement count have nowhere to live in a status column.
