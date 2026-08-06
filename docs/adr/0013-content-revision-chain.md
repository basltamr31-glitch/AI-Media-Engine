# ADR 0013: Preserve every revision; the draft-to-published diff is the product

- **Status:** Accepted
- **Date:** Phase 1.5

## Context

`ContentAsset` currently holds a single `body` and a `version` integer. Editing
an asset overwrites `body`.

That destroys the most valuable dataset this system can produce.

Consider what happens when a customer edits a generated LinkedIn post. The model
wrote one thing; the human published another. **The difference between those two
strings is a labelled preference pair, in that customer's own voice, produced as
a by-product of work they were doing anyway.** After a few hundred of them you
can few-shot a model into output that stops needing edits.

No general-purpose model has this. ChatGPT does not remember what this customer
fixed last Tuesday. It is unbuyable, unscrapeable, and it accumulates every day
the product is used. `STRATEGY.md` ranks it as the single strongest moat asset.

Overwriting `body` throws it away, silently, forever.

There is a second, duller reason: without revision history there is no answer to
"what exactly did we publish, and who approved that wording?" — which is an audit
requirement in the regulated verticals the strategy targets.

## Decision

`ContentAsset` becomes the **identity and current state** of a piece of content.
The text moves into an immutable chain:

```
ContentRevision
  id, account_id, workspace_id
  asset_id
  revision_number      monotonic per asset
  body                 the text at this revision
  origin               generated | edited | restored
  actor_id             who produced it (model runs as the `system` actor)
  parent_revision_id   what it was derived from
  model_route          which model/prompt version produced a generated revision
  created_at
```

`ContentAsset` keeps a `current_revision_id` pointer.

**Revisions are immutable.** An edit appends; it never mutates. Restoring an old
version appends a `restored` revision rather than moving a pointer backwards, so
the history reads as what actually happened.

**The diff is computed, not stored.** Given two revision IDs the delta is derived
on demand. Storing it would duplicate state that can drift.

**Generated revisions record their provenance** — which model, which prompt
version, which route. Without it the preference pairs cannot be attributed to a
model version, and a style regression after a prompt change is undiagnosable.

Transitioning an asset to `PUBLISHED` emits a signal (ADR 0012) carrying the
revision id and the edit distance from the last generated revision. That single
number — *how much did the human have to change?* — is the headline quality
metric for the whole generation phase.

## Consequences

**Good**

- The style dataset accumulates automatically from Phase 3, with no feature
  built for it and no user action required.
- "Edit distance from generated to published" becomes a measurable quality
  metric, per workspace, per prompt version, over time.
- Full audit trail: every wording that ever existed, who made it, what it came
  from.
- A/B testing prompt versions becomes possible, because output is attributable
  to a prompt version.

**Costs, accepted**

- Storage grows with edits rather than with assets. Text is small; a year of
  daily content is megabytes.
- One extra join to read current content. Mitigated by the `current_revision_id`
  pointer, which makes the common read a single indexed lookup.
- Two writes on an edit (revision insert + pointer update) inside one
  transaction. The unit of work already provides that.

## Alternatives considered

**Keep `version` as an integer and overwrite `body`.** The status quo. Rejected
outright: it discards the moat asset. This is the one decision in Phase 1.5 that
is genuinely irreversible if deferred.

**Store only the diff, not full bodies.** Saves space, costs reconstruction
complexity and makes any single revision unreadable without replaying the chain.
Text is cheap; clarity is not.

**Use git for content history.** Genuinely tempting — it is a revision system
that already exists. Rejected because content is per-tenant, queried
relationally, joined to stories and signals, and needs row-level access control.
A git repository per workspace is an operational burden with no relational query
path.

**Rely on the signal log alone to capture edits.** The signal records *that* an
edit happened; it should not carry the full before/after text as payload. Signals
are events; revisions are state. Conflating them makes both worse.
