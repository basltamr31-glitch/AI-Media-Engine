# ADR 0014: Domain knowledge lives in versioned packs, never in the engine

- **Status:** Accepted
- **Date:** Phase 1.5

## Context

The engine is currently an *AI news* system in ways that are easy to miss because
they look like ordinary modelling:

- `Story.entities` documented as "Organisations, models, people"
- `EditorialPolicy.priority_topics` seeded with "foundation models", "AI agents"
- A proposed `Category` enum with `MODEL_RELEASE`, `RESEARCH`, `FUNDING`
- Ranking signals chosen for how AI news behaves

Each is defensible in isolation. Together they mean that "the same engine works
for cybersecurity" is a claim we could not honour without editing Python.

The commercial case for honouring it is strong (`STRATEGY.md`): a cybersecurity
or pharma pack sells at 10–40× the AI-news price against incumbents charging
$10–20k/year, and the engineering delta is configuration.

The risk of *not* deciding now is ordinary technical debt with an unusual blast
radius: Phase 2 writes extraction, entity resolution, taxonomy and ranking. If
those are written against AI-news assumptions, un-picking them later means
rewriting the intelligence layer — the most valuable code in the repository.

## Decision

**A `DomainPack` is a versioned bundle of configuration that turns the generic
engine into a specific product.** The engine ships with no subject-matter
knowledge whatsoever.

```
Engine (code)                      Domain Pack (data)
─────────────                      ──────────────────
transports: email / rss / api      entity types + gazetteer
stage contract + runner            taxonomy tree
similarity algorithm               ranking signal definitions + weights
ranking framework                  channel definitions
generation framework               output-product definitions
review workflow                    prompt templates
ledger, evidence, provenance       brand voices
```

A pack has a `key`, a `version` and a checksum. Workspaces reference a pack
version, so upgrading a pack is an explicit, reversible act rather than a
surprise — and two workspaces can run different versions during a migration.

**`packs/ai-news/` is the first pack**, seeded from the values currently
hard-coded in enums and config. AI news becomes the first customer of our own
platform rather than the shape of it.

**Loading is a port.** `PackLoader` has a filesystem adapter now; a database or
remote registry adapter later needs no caller changes.

**Vertical-agnosticism is proven by a test, not by assertion.** The suite
includes a *synthetic* pack — deliberately absurd, unrelated to any real domain —
and an integration test that runs the whole pipeline under it. If any AI-news
assumption leaks into the engine, that test fails. No second real pack is built
speculatively (`NON_GOALS.md`).

## Consequences

**Good**

- A second vertical is a bundle of YAML and a gazetteer, not a branch.
- Each pack is a product SKU with independent pricing and an independent moat,
  since evidence and edit deltas accumulate per pack.
- Pack versioning makes taxonomy and weight changes reviewable and revertible.
- The engine stays small. Domain complexity accumulates in data, where
  non-engineers can eventually edit it.

**Costs, accepted**

- Indirection. "Why is this story classified this way?" requires reading a pack,
  not a Python file. Mitigated by `engine pack show`, which renders the resolved
  pack.
- Pack contents need validation, since a typo in YAML is now a runtime concern
  rather than a compile-time one. Every pack is schema-validated on load and by
  `engine pack validate` in CI.
- Some genuinely universal concepts will be put in packs by mistake and have to
  be promoted back into the engine. The test for what belongs where: *would a
  cybersecurity product need this to be different?* If yes, pack. If no, engine.

## Alternatives considered

**Keep it AI-specific and fork later for a second vertical.** Two codebases
diverging immediately, with every engine improvement needing to be applied twice.
This is how agencies end up with fifteen unmaintained forks.

**Make everything configurable, with no packs — just a large settings surface.**
Configuration without bundling and versioning is exactly the current YAML
situation at greater scale: no way to say "this workspace runs AI-news v3", no
way to ship a coherent set of defaults, no way to roll back a taxonomy change.

**Defer until a second vertical is actually sold.** The strongest counter-argument,
and it is why the pack abstraction ships now while a *second pack* explicitly
does not. The cost of the abstraction is a table and a loader. The cost of
retrofitting it after Phase 2 is the intelligence layer.
