# ADR 0018: The engine records publication; it does not perform it

- **Status:** Accepted
- **Date:** Phase 5

## Context

Phase 5 closes the loop: content is reviewed, published, and what it achieved is
fed back into ranking. The obvious reading of "published" is that the engine
posts to LinkedIn, X and Instagram itself — OAuth per platform, a scheduler, a
retry queue, token refresh, per-platform rate limits and per-platform failure
modes.

That reading is wrong for this product at this stage, and the reason is not
effort. It is that **auto-publishing and the feedback loop are separable, and
only one of them is the asset.**

What the feedback loop actually requires is a durable link between a
`ContentAsset` and an outcome. Given a `Publication` row with an id, a channel
and a timestamp, engagement can be attached to it and joined back through the
asset to the story, the score, the signals that produced the score, and the
revision a human approved. None of that depends on who pressed *post*.

Three further pressures point the same way:

- **Platform APIs are the least stable part of the system.** Posting endpoints,
  scopes and review processes change on the platforms' schedule, not ours. A
  broken integration would stop the loop that the whole phase exists to close.
- **Review exists for a reason.** The state machine already refuses to let
  anything reach a channel without a human decision. An automatic post
  immediately after approval narrows that gate to the width of a typo.
- **It is not where the differentiation is.** Every scheduling tool posts to
  LinkedIn. None of them can tell you which of your stories was worth writing.

## Decision

The engine records publication as a **fact reported by a human**.

`engine publish record <asset-id> --url ...` moves an approved asset to
`published` and writes a `Publication` row. `published_by` is `"manual"`.
`published_at` may be backdated, because posts get recorded after the fact and
stamping them with the moment of data entry would corrupt every timing analysis
the log exists to support.

The complement is `engine export run`, which writes each approved post as a file
containing exactly the text to paste, formatted to the channel's rules from the
pack. Writing that file changes no state: it is not evidence that anyone posted
anything.

Engagement is recorded the same way — `engine metrics add`, `collected_by` of
`"manual"`, one row per observation.

Three consequences follow deliberately:

1. **No platform credentials exist anywhere in the system.** There is no OAuth
   flow to publish, no token to leak, and no scope to over-request.
2. **The review gate cannot be widened by accident.** Publication requires a
   human twice: once to approve, once to state that it went out.
3. **Automating collection later changes a caller, not a schema.**
   `published_by` and `collected_by` already distinguish manual from automatic,
   so a platform integration writes the same rows with a different value in one
   column. The report, the signals and anything learning from them are
   untouched.

### Why these are not pipeline stages

`export` is a stage: unattended batch work over everything eligible, with a
ledger entry and a cost line like every other stage.

`publish record` and `metrics add` are not. A stage processes a batch; these are
one human stating one fact about one artifact, and the *reason* — a rejection's
cause, the URL a post ended up at — has nowhere to live inside a batch loop.
They are use cases in `engine.cli.lifecycle`, alongside `approve`, `reject` and
`edit`, which are one-human-one-decision for the same reason. The CLI is the
application layer (ADR 0002), so that is where they belong until an API exists
to call them too.

## Consequences

**Good**

- The feedback loop is complete now rather than after per-platform integration
  work, and it is complete for *every* channel including ones with no API at all.
- Nothing in the system can post on a user's behalf, which removes an entire
  category of both security exposure and accidental publication.
- Adding a channel needs no integration: a pack entry gives it export
  formatting, and recording a publication already works for anything with a URL.
- The `Publication` and `PerformanceMetric` schemas are decided by what the loop
  needs, not by what one platform's API happens to return.

**Costs, accepted**

- Publishing is manual, so the operator does the last mile. At current volume —
  a handful of posts a day, each already requiring a human read — this is a
  small part of the work.
- Engagement numbers are typed in, so they are as timely and as accurate as
  whoever types them. `captured_at` is recorded separately from `recorded_at`
  precisely so late entry stays visible instead of being silently absorbed.
- A human can forget to record a publication, in which case the loop has a hole
  for that post. `engine review queue --state approved` shows what is approved
  and not yet recorded, which makes the hole visible rather than preventing it.

## Alternatives considered

**Full auto-publishing with per-platform OAuth.** The version that looks most
finished and delivers the least. It closes nothing until the last integration
lands, breaks on the platforms' schedule rather than ours, and produces exactly
the same `Publication` rows the manual path produces today.

**A webhook or Zapier-style hand-off.** Cheaper than direct integration, and it
moves the failure into a system we cannot observe. The engine would have to
either assume the post went out — recording something it does not know — or ask
a human anyway, which is the current design with more moving parts.

**Scrape engagement from the platforms.** Fragile, generally against terms of
service, and it would put the credentials back that this decision removes.

**Skip publication records; attach engagement to the asset.** One fewer table.
Rejected because an asset can go live on more than one channel, and collapsing
them makes "which channel worked?" unanswerable — which is one of the two
questions the loop is being built to answer.
