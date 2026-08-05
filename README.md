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

## Status: Phase 0 complete

Phase 0 is the foundation: domain model, persistence, migrations, configuration,
pipeline orchestration, observability, CLI, tests and architectural enforcement.
No ingestion, no LLM calls, no generation yet — those are Phases 1–5.

The system runs end to end today with zero stages registered, which proves the
spine works before any business logic can hide a defect in it.

| Phase | Scope | Status |
|-------|-------|--------|
| **0** | Foundation: domain, storage, pipeline spine, CLI, tests | ✅ Complete |
| 1 | Ingestion: Gmail, RSS, HTTP APIs → `SourceDocument` | Pending approval |
| 2 | Extraction, story resolution, primary-source enrichment | Not started |
| 3 | Editorial scoring, LLM generation, brand voice | Not started |
| 4 | Rendering: Markdown + JSON output | Not started |
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

Then:

```bash
engine pipeline run --smoke      # end-to-end run with a no-op stage
engine pipeline runs             # run history with duration and cost
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
| `engine workspace seed` | Reconcile the database with the workspace YAML |
| `engine workspace list` / `sources` | Inspect tenants and their configured feeds |
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
