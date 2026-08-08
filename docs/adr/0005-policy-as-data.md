# ADR 0005: Brand voice and editorial policy are data, not code

- **Status:** Accepted (channel specs later moved into the pack by ADR 0015;
  see the amendment under Decision)
- **Date:** Phase 0

## Context

The system's behaviour is defined by things that read like content rather than
logic: the Arabic voice, tone rules, forbidden topics, ranking weights, how many
stories to select, which sources to trust, per-channel formatting constraints.

The default instinct is to put these in Python — constants, prompt templates,
scoring functions. That works for exactly one workspace.

The moment a second workspace exists — the first paying customer — every one of
those values must vary per tenant. If they live in code, onboarding a customer
means a code change, a review, and a deployment. That single fact is what
separates "my script" from "a platform", and it is the most common reason
promising internal tools never become products.

There is a second reason. Ranking weights are meant to be **fitted against
measured engagement** once the feedback loop is running. Weights embedded in a
function cannot be tuned by a script, versioned per tenant, or A/B tested.

## Decision

All behavioural policy lives in validated YAML under `config/`:

```
config/workspaces/<slug>.yaml       tenant, sources, editorial policy
config/brand_profiles/<key>.yaml    voice, tone, audience, terminology
config/channel_specs/<key>.yaml     platform constraints
```

> **Amended by ADR 0015.** `config/channel_specs/` no longer exists. Channel
> rules moved into the domain pack as `ChannelDef`, because a platform's
> character limit is a fact about the platform rather than a tenant preference —
> the reasoning this ADR gives below for not tenant-scoping them, followed to
> its conclusion. The rest of this decision stands: workspaces and brand
> profiles are still validated YAML under `config/`.

Corresponding domain entities: `EditorialPolicy`, `ScoringProfile` (ADR 0016),
`BrandProfile`, and `ChannelDef` in the pack (ADR 0015). Loading is via
`PolicyLoader`, which validates on read — a malformed brand profile fails at
startup with a precise error rather than producing subtly wrong content
overnight.

`engine workspace seed` reconciles the database with these files idempotently:
the file is the source of truth, the database a materialised view.

Channel specs are deliberately **not** tenant-scoped: X's character limit is a
fact about X, shared by everyone.

## Alternatives considered

**Python constants and prompt template files.** Simplest for one tenant.
Rejected: makes customer onboarding a deployment.

**Database-only policy with an admin UI.** Where this ends up eventually.
Rejected for now: no UI exists, and YAML in version control gives history,
review and diffs for free — all of which matter while the voice is being tuned.

**A prompt-management SaaS.** External dependency, monthly cost, and it splits
the source of truth across two systems.

## Consequences

**Positive**

- Adding a workspace is adding a file. No deployment.
- Voice changes are reviewable diffs with attribution and history.
- Weights are tunable by a script once performance data exists.
- Policy files are validated in CI, so a broken profile fails a test rather than
  a production run.

**Negative**

- Indirection: understanding behaviour means reading YAML as well as Python.
- YAML permits structures the schema rejects; mitigated by validating on load and
  testing the shipped files directly.
- Migrating policy from files to a database (Phase 7) is future work — but it is
  a data migration between two data stores, not a refactor of application logic,
  which is the point.
