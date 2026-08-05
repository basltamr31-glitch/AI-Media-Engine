# ADR 0001: Record architecture decisions

- **Status:** Accepted
- **Date:** Phase 0

## Context

This project is intended to become a commercial product and serve as a flagship
portfolio piece. Both audiences — a future collaborator and a future reviewer —
need to know *why* the code is shaped the way it is, not just what it does.

Decisions made in week one determine what is possible in year two. Multi-tenancy,
provenance and cost accounting are cheap now and near-impossible to retrofit.
Those trade-offs need to be written down while the reasoning is fresh, including
the alternatives that were rejected and why.

## Decision

Record each significant architectural decision as a numbered Markdown file in
`docs/adr/`, following Michael Nygard's format: Context, Decision, Consequences.

A decision is significant if reversing it later would require changing multiple
modules, a data migration, or a change in how the product is sold.

ADRs are immutable once accepted. A changed decision gets a new ADR that
supersedes the old one; the original stays as a record of what was believed at
the time.

## Consequences

**Positive**

- New contributors can read the reasoning instead of reverse-engineering it.
- Revisiting a decision starts from its actual rationale, so genuinely changed
  circumstances are distinguishable from forgotten context.
- The decision log itself demonstrates engineering judgement.

**Negative**

- Small ongoing documentation cost.
- Risk of drift if a decision changes without a superseding ADR. Mitigated by
  reviewing ADRs at each phase boundary.
