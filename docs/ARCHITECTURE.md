# Architecture

## The thesis this architecture serves

The pipeline — newsletter in, social post out — is a commodity. Anyone can build
it in a weekend and the underlying intelligence gets cheaper every quarter. Three
things are not commodities, and the architecture exists to protect them:

1. **Arabic.** Native-quality Arabic AI content is genuinely underserved. This is
   the wedge, so brand voice is a first-class per-language entity, not a prompt
   suffix.
2. **Editorial judgement.** Choosing 3 stories from 40 is the durable IP. Ranking
   is therefore explainable, tunable and stored as data.
3. **The feedback loop.** Competitors generate blind. Instrumenting
   publish → engagement → ranking builds a dataset that cannot be copied by
   cloning prompts.

Every decision below traces back to one of these.

---

## Layers

Dependencies point inward only. Enforced by `.importlinter`, checked with
`lint-imports`, so the boundary cannot silently rot.

```
┌──────────────────────────────────────────────────────────┐
│ engine.cli            composition root, user interface   │
├──────────────────────────────────────────────────────────┤
│ engine.pipeline       stage orchestration, run ledger    │
├──────────────────────────────────────────────────────────┤
│ engine.adapters       persistence · config · sources · llm│
├──────────────────────────────────────────────────────────┤
│ engine.observability  structured logging                 │
├──────────────────────────────────────────────────────────┤
│ engine.settings       configuration                      │
├──────────────────────────────────────────────────────────┤
│ engine.core           entities · ports · policies (no I/O)│
└──────────────────────────────────────────────────────────┘
```

`engine.core` may not import `sqlalchemy`, `typer`, `structlog`, `yaml`,
`anthropic` or `httpx`. That constraint is what allows the same domain to be
driven by a CLI today and an HTTP API later without modification.

### Why one distribution instead of four packages

The approved design described four separately installable packages
(`core`, `adapters`, `pipeline`, `evals`). Phase 0 ships **one** distribution
with the same logical boundaries, enforced by import-linter rather than by
distribution metadata.

The boundaries are identical and equally enforced; what disappears is four
`pyproject.toml` files, four editable installs and cross-package version
coordination for a single-developer project. Splitting later is a mechanical
move — the import graph already proves nothing crosses a boundary illegally.
This is recorded as [ADR 0002](adr/0002-single-package-layered-monolith.md).

---

## Domain model

```
Workspace ─┬─ Source ──── SourceDocument ──── StoryCandidate ─┐
           │                                                  ├─▶ Story ──┬─ Evidence
           ├─ BrandProfile                                     │           ├─ EnrichedSource
           ├─ EditorialPolicy                                  │           └─ EditorialScore
           └─ RunLedger ─── StageRecord                        │
                                                    ContentAsset ─── Publication ─── PerformanceMetric
```

| Entity | Why it exists |
|---|---|
| `Workspace` | Tenant root. On every table from day one. |
| `Source` | A configured feed. `kind` is the *transport*, `parser_key` the *format*. |
| `SourceDocument` | Immutable fetched artifact. Enables replay and audit without re-fetching. |
| `StoryCandidate` | One story as one source told it. Deliberately not canonical. |
| `Story` | The canonical, deduplicated, enriched story. The core object. |
| `EnrichedSource` | Content from the *primary* source. Enforces "tips, not text". |
| `Evidence` | Claim ↔ source binding. The provenance ledger. |
| `EditorialScore` | Explainable ranking with per-signal components. |
| `BrandProfile` | Voice as data. The change that makes multi-tenancy possible. |
| `ChannelDef` | Platform constraints, declared by the pack. Not tenant-scoped — facts about the platform (ADR 0015). |
| `ContentAsset` | One artifact per story × channel × language, versioned, state-machined. |
| `Publication` | Immutable record of going live. Recorded by a human, not performed (ADR 0018). |
| `PerformanceMetric` | Closes the feedback loop. |
| `RunLedger` / `StageRecord` | Observability and unit economics. |

### Invariants encoded in types, not conventions

- `Evidence.quote` rejects anything over 25 words → fair-use ceiling cannot be
  forgotten under deadline pressure.
- `Story.is_publishable` is false without `enrichment` → we never write about a
  story we have only read about second-hand.
- `ContentAsset.transition_to` refuses illegal transitions → nothing reaches a
  channel without passing the human review gate.
- A `ScoringProfile`'s weights must sum to 1.0, and every key must be a ranking
  signal the active pack declares → ranking stays interpretable (ADR 0016).
- `TenantScoped` carries `workspace_id` → isolation is structural.
- `Publication` and `PerformanceMetric` have no update mapper and no update
  repository method → an outcome cannot be quietly revised, so a correction
  is a second observation and the series survives.

---

## Pipeline

Every roadmap phase adds **stages**, never a new pipeline:

```python
class Stage(Protocol):
    @property
    def name(self) -> str: ...
    def run(self, context: RunContext) -> StageResult: ...
```

Phase 5's other two capabilities -- recording that content was published, and
recording what it achieved -- are deliberately *not* stages, which is why the
table below stops at `export`. A stage is unattended batch work over everything
eligible; both of those are one human stating one fact, and the reason a fact
was recorded has nowhere to live inside a batch loop. They are use cases in
`engine.cli.lifecycle` (ADR 0018), alongside approve, reject and edit.

`PipelineRunner` executes stages in order and, for each one, records start,
finish, item counts, token usage, cost and errors — committed incrementally, so
a crashed run still leaves evidence of how far it got. A per-run cost ceiling
aborts runaway loops.

Because this is written once in Phase 0, every future stage inherits
observability, cost accounting and failure handling for free.

Planned stage sequence:

| Phase | Stages |
|---|---|
| 1 | `ingest` |
| 2 | `extract`, `resolve` (clustering + primary-source enrichment) |
| 3 | `editorial`, `generate` |
| 4 | `render` |
| 5 | `export` |

---

## Storage

SQLAlchemy 2.0 with Alembic. SQLite locally; the schema is restricted to a
**Postgres-compatible subset**, so moving to a hosted database is a
connection-string change.

Concretely: no SQLite-only types or behaviour, JSON columns declared as a variant
that becomes `JSONB` on Postgres, every constraint explicitly named (Alembic
cannot reliably alter unnamed constraints), `workspace_id` indexed on every
table, and enums stored as strings so adding a value is not a migration.

Domain objects are mapped to ORM rows explicitly rather than being ORM-backed.
The boilerplate buys a domain free of SQLAlchemy, entities that cannot trigger
lazy database I/O from inside business logic, and a storage schema that can
evolve independently of the domain shape.

The Phase 0 migration creates only what Phases 0–1 need: `workspaces`,
`sources`, `source_documents`, `runs`, `run_stages`. Later phases add their own
tables in their own migrations — schema evolves with need, and migration
discipline is demonstrated rather than claimed.

---

## Policy as data

Brand voice, editorial weights and source lists live in `config/*.yaml`,
validated on load.

```
config/
├── workspaces/default.yaml        tenant + sources + editorial policy
├── brand_profiles/default_ar.yaml Arabic voice
└── brand_profiles/default_en.yaml English voice
```

Channel rules are **not** here. They moved into the domain pack with ADR 0015:
X's character limit is a fact about X, not a tenant preference, so it belongs
with the vocabulary rather than with the customer.

```
packs/ai-news/pack.yaml            taxonomy, products, channels, ranking signals
evals/golden/*.yaml                golden cases; evals/baseline.json the expectation
```

`engine workspace seed` reconciles the database with these files idempotently:
the file is the source of truth, the database a materialised view. Adding
workspace #50 is adding a file. If policy lived in Python, every new customer
would be a deployment — the single change that most directly blocks monetisation.

---

## Extension points

| Seam | Contract | Populated in |
|---|---|---|
| `SourceFetcher` | one transport (email, RSS, API) → `SourceDocument` | Phase 1 |
| `DocumentParser` | one format → `StoryCandidate[]` | Phase 2 |
| `ParserRegistry` / `FetcherRegistry` | key → implementation | Phase 1 |
| `engine.fetchers` / `engine.parsers` entry points | installed package → registry entry | Phase 1.5 |
| `Source.config` | per-source options, passed to fetcher and parser alike | Phase 2 |
| `LLMPort` | structured generation with usage accounting | Phase 3 — local (Ollama) and hosted (Anthropic) implementations, selected by config |
| `Stage` | one unit of pipeline work | every phase |
| `Clock` | injectable time | now |
| `UnitOfWork` + repositories | transactional persistence | now |

Adding a newsletter is: write a parser, register it, add a config row. The
pipeline never learns the word "TLDR", and losing any single source degrades
quality instead of stopping production.

Registration goes through entry points (ADR 0017), so "register it" does not
mean editing this repository — the engine's own fetchers are declared the same
way any third-party package would declare one. See
[`docs/PLUGINS.md`](PLUGINS.md).

---

## Decisions supporting future monetisation

| Decision | What it unlocks |
|---|---|
| `workspace_id` everywhere from commit one | Multi-tenancy without a rewrite |
| Policy as data | Onboarding a customer is config, not deployment |
| Provenance ledger | Legal defence, trust feature, debugging |
| Persisted stage records | Replay, evals, distributed execution later |
| Cost accounting per run | Unit economics before the first invoice |
| Feedback-loop schema | The dataset that becomes the moat |
| Model-agnostic `LLMPort` | Provider churn is an adapter change |
| Explicit review state machine | Accountability that clients require |
| Edit deltas measured at capture | The preference dataset, as a by-product of review |

---

## What is deliberately absent

Auto-publishing, image generation, video rendering, SEO blog drafts, a web UI,
authentication, billing and fine-tuning. Each is postponed for a stated reason
in the roadmap.

Auto-publishing is the one with its own decision record rather than a roadmap
line: the engine *records* publication and never performs it
(ADR 0018). That is not a deferral -- the feedback loop needs the link between
an asset and an outcome, and posting on someone's behalf is neither necessary
for it nor where any differentiation lies.

The discipline: **postpone features, never postpone architecture.** Adding a
feature later is a sprint. Retrofitting tenancy, provenance or cost accounting
is a rewrite.
