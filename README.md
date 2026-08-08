# AI Media Engine

A production-grade content engine that ingests AI news from multiple sources,
decides what actually matters, verifies it against primary sources, and produces
publication-ready content in Arabic and English across multiple channels.

**Not** a newsletter-to-social-post script. The pipeline is the commodity; the
defensible parts are editorial judgement, native-quality Arabic, verifiable
provenance, and a closed feedback loop from published content back into ranking.

```
                        ┌──────────────── FEEDBACK LOOP ─────────────────┐
                        │                                                 ▼
┌─────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌────────┐   ┌─────────┐
│ INGEST  │──▶│ EXTRACT  │──▶│ RESOLVE  │──▶│ EDITORIAL│──▶│GENERATE│──▶│ REVIEW  │
│ sources │   │ candidate│   │ cluster  │   │  score   │   │ voice ×│   │ human   │
│         │   │ stories  │   │ + enrich │   │  select  │   │ channel│   │  gate   │
└─────────┘   └──────────┘   └──────────┘   └──────────┘   └────────┘   └────┬────┘
                                   ▲                                          │
                             PRIMARY SOURCE                             ┌─────▼──────┐
                            (paper / blog / API)                        │ DISTRIBUTE │
                                                                        │  MEASURE   │
                                                                        └────────────┘
```

---

## Status: Phase 4 complete

Phase 0 built the spine: domain model, persistence, migrations, configuration,
pipeline orchestration, observability, CLI, tests and architectural enforcement.

Phase 1 makes it ingest. Gmail newsletters, RSS/Atom feeds and JSON APIs are
fetched, deduplicated by content hash, archived verbatim and stored as
`SourceDocument` records — idempotently, with per-source fault isolation.

Phase 1.5 made the engine vertical-agnostic: domain packs supply the taxonomy,
scoring signals are declared rather than hard-coded, and fetchers and parsers
are discovered through entry points, so extending the engine is a package
install rather than a fork.

Phase 2 turns documents into stories. Newsletters, feeds and API items are
parsed into candidates with their outbound links (advertising removed);
candidates are clustered into canonical stories; each story's primary source is
fetched and extracted; and a provenance ledger records which sources support
which claim.

Phase 3 makes it write. Stories are scored against declared signals and
selected against editorial gates; a selected story becomes one post per brand
voice, channel and language, through an `LLMPort` whose default implementation
is a **local model** — free, offline, and swapped for a hosted one by an
environment variable. Spend is recorded per generation, the ceiling is checked
before each call, and a golden set of algorithmic checks makes quality
regressions visible without costing anything to run.

Phase 4 makes it publishable. A day's output becomes a dated folder:
`package.json` as the canonical machine output and `post.md` as the human view
of it. After that command nothing downstream needs a database.

The engine now runs end to end: a newsletter arrives, and a folder of drafted
posts comes out the other side.

One honest caveat on "publication-ready". The capability is complete; the
quality bar is not met yet on the shipped local model, which scores **0.881**
on its own golden set with two open failures (copying from the source, and
overrunning X's 280-character limit). Both are limits of a 1.7B model rather
than of the prompt. That is the point of having built the evals and the local
provider first: quality became *measurable* before it became billable, so
moving to a hosted model changes a number rather than an opinion.

| Phase | Scope | Status |
|-------|-------|--------|
| **0** | Foundation: domain, storage, pipeline spine, CLI, tests | ✅ Complete |
| **1** | Ingestion: Gmail, RSS, JSON APIs → `SourceDocument` | ✅ Complete |
| **1.5** | Domain packs, open vocabularies, plugin discovery | ✅ Complete |
| **2** | Extraction, story resolution, primary-source enrichment, evidence | ✅ Complete |
| **3** | Editorial scoring, selection, LLM generation, brand voice, cost, evals | ✅ Complete |
| **4** | Rendering: dated editions, `package.json` + `post.md`, Arabic RTL | ✅ Complete |
| 5 | Review workflow, distribution export, performance capture | Not started |
| 6 | Hardening, evals, scheduling | Not started |
| 7 | Multi-tenant API, billing, self-serve | Not started |

---

## Quickstart

Requires **Python 3.12+**.

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows
# source .venv/bin/activate      # macOS / Linux

pip install -e ".[dev]"
copy .env.example .env           # optional; defaults work as-is

engine db upgrade                # create the schema
engine workspace seed            # load config/workspaces/default.yaml
engine doctor                    # verify the environment
```

Then ingest. RSS and the JSON APIs work immediately with no credentials:

```bash
engine ingest run                # fetch from every active source
engine ingest run                # again: stores nothing new (idempotent)
engine ingest status             # per-source document counts
engine pipeline runs             # run history with duration and cost
```

Then extract story candidates from what was ingested:

```bash
engine parse run                 # documents -> story candidates
engine parse status              # per-source counts and recent headlines
```

Then cluster the mentions, read what they point at, and write from it:

```bash
engine resolve run               # mentions -> canonical stories
engine stories list              # what the engine thinks happened
engine enrich run                # fetch each story's primary source
engine evidence run              # record what supports each story
engine score run                 # rank them, showing the working
engine select run                # choose what is worth producing
engine generate run              # write them, per channel and language
engine cost report               # what that cost, per asset and per attempt
engine eval run                  # did quality just regress?
engine render run                # write the day's edition to disk
```

How parsing decides what is a story, and how to add a parser:
[`docs/PARSING.md`](docs/PARSING.md).
Why a story is enriched or refused: [`docs/ENRICHMENT.md`](docs/ENRICHMENT.md).
What supports a story, and what `primary` means: [`docs/EVIDENCE.md`](docs/EVIDENCE.md).
How a story is ranked, and how to add a signal: [`docs/SCORING.md`](docs/SCORING.md).
Which model writes, and why it runs locally: [`docs/LLM.md`](docs/LLM.md).
How a story becomes a post, per channel and language:
[`docs/GENERATION.md`](docs/GENERATION.md).
How quality regressions are made visible: [`docs/EVALS.md`](docs/EVALS.md).
What a day's output folder contains, and why Arabic needs HTML:
[`docs/RENDERING.md`](docs/RENDERING.md).
How plugins are discovered: [`docs/PLUGINS.md`](docs/PLUGINS.md).

To include Gmail newsletters, authorise once (see [`docs/INGESTION.md`](docs/INGESTION.md)):

```bash
engine sources auth              # opens a browser for Google consent
```

Full instructions, including troubleshooting: [`docs/SETUP.md`](docs/SETUP.md).

---

## Commands

| Command | Purpose |
|---|---|
| `engine doctor` | Verify environment, database, migrations and config |
| `engine version` | Print the version |
| `engine config show` | Show effective settings (secrets redacted) |
| `engine db upgrade` / `downgrade` / `current` / `history` | Schema migrations |
| `engine db revision -m "..."` | Generate a migration from model changes |
| `engine pack list` / `show` / `validate` | Domain packs: taxonomy and vocabulary |
| `engine account list` / `show` | Accounts, actors and memberships |
| `engine signals summary` / `show` | The feedback log behind ranking |
| `engine workspace seed` | Reconcile the database with the workspace YAML |
| `engine workspace list` / `sources` | Inspect tenants and their configured feeds |
| `engine sources auth` / `logout` / `status` | Google OAuth and credential readiness |
| `engine ingest run` | Fetch new documents from every active source |
| `engine ingest status` | Per-source document counts and last-fetch times |
| `engine parse run` / `status` | Extract story candidates from ingested documents |
| `engine resolve run` | Cluster story candidates into canonical stories |
| `engine stories list` / `show` / `reset` | Inspect and re-cluster canonical stories |
| `engine enrich run` / `status` | Fetch the primary source behind each story |
| `engine evidence run` / `show` | The provenance ledger behind each story |
| `engine score run` / `top` / `show` | Rank stories and explain the ranking |
| `engine select run` / `list` | Choose what is worth producing, and say why not |
| `engine llm check` | Prove the configured model answers, and conforms to a schema |
| `engine generate run` | Write a post per brand voice, channel and language |
| `engine generate list` / `show --history` | Generated assets and their revision chain |
| `engine cost report` | Spend per asset and per attempt, and recent runs |
| `engine eval list` / `run` | Score generation against the golden set |
| `engine render run` / `show` | Write and inspect the day's edition |
| `engine plugins list` | Discovered fetchers and parsers, and what failed to load |
| `engine pipeline run` | Execute the pipeline |
| `engine pipeline runs` / `stages <run-id>` | Inspect run history and per-stage cost |

---

## Architecture at a glance

Dependencies point inward only, and the rule is machine-enforced:

```
engine.cli            composition root, user interface
engine.pipeline       stage orchestration, run ledger, cost ceiling
engine.adapters       persistence, config, sources, LLM — all the I/O
engine.observability  structured logging
engine.settings       configuration
engine.core           pure domain: entities, ports, policies (zero I/O)
```

```bash
lint-imports    # fails if any layer boundary is violated
```

Full rationale: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
C4 views, the domain model and the runtime sequence:
[`docs/DIAGRAMS.md`](docs/DIAGRAMS.md).
Individual decisions with context and trade-offs: [`docs/adr/`](docs/adr/).

---

## Development

```bash
pytest                       # tests
pytest -m "not integration"  # unit tests only
ruff check . && ruff format --check .
mypy
lint-imports
```

---

## Editorial and legal position

Newsletters are **tips, not text**. They tell us *what happened*; the primary
source tells us *what to say*. Every published claim links to a primary source
recorded in the provenance ledger, quotes are capped at 25 words (enforced in
the domain model, not by convention), and content that adds nothing to someone
who read the original does not ship.

This is a product decision as much as a legal one: republishing is a commodity,
and commodities do not build durable brands.
