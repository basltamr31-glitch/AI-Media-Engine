# ADR 0011: Account sits above Workspace; Actor records who acted

- **Status:** Accepted
- **Date:** Phase 1.5

## Context

ADR 0003 established `workspace_id` on every table from the first commit, so
tenant isolation is a schema property rather than a convention. That decision
held: Phase 1 added five tables and every query is scoped without anyone having
to remember.

But a workspace is not a customer. Run the agency test:

> A marketing agency manages 80 brands. Each brand has its own voice, sources,
> channels and approval workflow. One invoice. One set of operators. One quota.

Under the current model that agency is **80 unrelated tenants**. There is no
object that owns them, so there is nowhere to hang a plan, a quota, a billing
relationship, or a list of people permitted to act. Shared sources would be
duplicated 80 times. Cross-brand reporting is impossible.

Separately, nothing records **who** did anything. `ContentAsset.review_notes`
holds *what* a reviewer said; no column holds who they were. That blocks two
things: an audit trail (required in regulated verticals — see `STRATEGY.md`) and
per-editor preference learning, since two editors on one workspace have
different taste and averaging them learns neither.

## Decision

Introduce three entities above and beside the workspace:

```
Account ──┬── Workspace ── (all existing tenant-scoped data)
          │
          └── Membership ── Actor
```

**`Account`** — the billing and quota root. Owns many workspaces. Holds plan,
limits and the commercial relationship.

**`Actor`** — anyone or anything that takes an action: a human operator, the
scheduler, the pipeline itself. A `system` actor is seeded so every automated
action has an attributable origin rather than a null.

**`Membership`** — links an actor to an account with a role. Roles are stored as
strings, not enumerated, for the reasons in ADR 0015.

`Workspace` gains `account_id`, which is the ownership link. Beyond that,
`account_id` is denormalised **only onto tables that are actually queried by
account** — starting with `signals` (ADR 0012), the highest-volume table and the
one feeding cross-brand analytics and metering.

Everything else reaches its account by joining through the workspace. Copying
the column onto `sources`, `source_documents`, `runs` and `run_stages` would be
denormalisation without a query to justify it, and every denormalised column is
a consistency obligation. The rule applied: **denormalise when a real query
demands it, not in anticipation of one.**

The migration backfills a `default` account owning the existing workspace, so
nothing breaks and the change is invisible to current use.

## Consequences

**Good**

- The agency customer — the highest-LTV segment — becomes expressible without a
  tenancy migration later.
- Quotas, plans and usage metering have somewhere to live before billing exists.
- Every action becomes attributable, which is a prerequisite for both audit and
  per-editor style learning.
- Shared configuration (a source list used by 80 brands) can be defined once at
  account level and inherited.

**Costs, accepted**

- One more table in every join path, and one more identifier in log lines.
- `account_id` denormalised onto tenant tables must be kept correct. It is set at
  insert and never updated, because a workspace does not change accounts.
- Three tables built before a single customer exists. This is the same bet as
  ADR 0003, made for the same reason: the cost is one migration now versus a
  migration across every table, with live customer data, later.

## Alternatives considered

**Defer until the first agency customer.** The argument that killed it is the
same one in ADR 0003: retrofitting a tenancy level means migrating every table
that already has rows, under time pressure, with a customer waiting. The cost
asymmetry is roughly 30:1.

**Make `Workspace` itself hierarchical (parent/child).** Fewer tables, and
genuinely tempting. Rejected because it conflates two different things: a
workspace is an *editorial context* (voice, sources, policy) while an account is
a *commercial context* (billing, quota, people). Overloading one entity with
both means every query has to know which sense it means.

**Put billing in a separate service.** Premature. There is no billing, and a
service boundary before a domain boundary is backwards.
