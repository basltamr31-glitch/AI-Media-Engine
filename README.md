# AI Media Engine

A content engine that reads AI newsletters, feeds and APIs, decides what
actually matters, verifies it against the **primary source**, and writes
publication-ready posts per channel and language — with a human approval gate,
a provenance ledger, and cost accounting from the first commit.

**Not** a newsletter-to-social-post script. The pipeline is the commodity. The
parts worth building are editorial judgement, verifiable provenance, and a
closed feedback loop from published content back into ranking.

```
┌─────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌────────┐   ┌────────┐
│ INGEST  │──▶│  PARSE   │──▶│ RESOLVE  │──▶│ EDITORIAL│──▶│GENERATE│──▶│ REVIEW │
│ email   │   │ candidate│   │ cluster  │   │  score   │   │ voice ×│   │ human  │
│ rss·api │   │ stories  │   │ + enrich │   │  select  │   │ channel│   │  gate  │
└─────────┘   └──────────┘   └──────────┘   └──────────┘   └────────┘   └───┬────┘
                                  ▲                                         │
                            PRIMARY SOURCE                        ┌─────────▼────────┐
                          (paper · blog · API)                    │ EXPORT · RECORD  │
                                  │                               │ MEASURE · TUNE   │
                                  └───────────── feedback ────────└──────────────────┘
```

> **Status: a working engine, not a finished product.** Phases 0–6 are complete
> and tested. It runs end to end, unattended, on a schedule. The output is good
> enough to *review*, not yet good enough to publish unread — and the section
> below says exactly why, with numbers. Automated publishing to social platforms
> is **not** implemented and is deliberately deferred.

---

## What it actually does today

| | |
|---|---|
| **Ingests** | Gmail newsletters (OAuth, read-only), RSS/Atom, JSON APIs — deduplicated by content hash, archived verbatim |
| **Parses** | TLDR, arXiv, Hacker News → story candidates, advertising removed |
| **Resolves** | Clusters the same story across sources into one canonical `Story` |
| **Enriches** | Fetches and extracts the **primary source** — the paper, not the newsletter's summary |
| **Verifies** | Records which source supports which claim; quotes capped at 25 words in the domain model |
| **Ranks** | Explainable scoring against declared signals, every component stored |
| **Writes** | One post per brand voice × channel × language, through a swappable LLM port |
| **Reviews** | Human approve / reject / edit over a state machine, every decision attributed and logged |
| **Exports** | Publish-ready files per channel |
| **Records** | That you published, and what it achieved |
| **Tunes** | Correlates ranking signals against measured engagement, and *proposes* new weights |

Channels: **LinkedIn, X, Instagram.** Languages: **English and Arabic.**
Both are configuration, not code.

### Runs free, by design

The default model is **local** (Ollama, `qwen3:1.7b`). Every run costs
`$0.000000`. A hosted model is one environment variable away —
`ENGINE_LLM_PROVIDER=anthropic` — with no code change, because cost accounting
and the provider port were built before either was needed.

---

## Honest status

This section exists because "done" means four different things and conflating
them is how projects mislead people.

| Level | | |
|---|---|---|
| 1 | Capability implemented | ✅ Phases 0–6 |
| 2 | Capability tested | ✅ ~1,200 tests, strict typing, enforced architecture |
| 3 | **Quality bar met** | ❌ **Not yet** — see below |
| 4 | **Production-proven** | ❌ **Not yet** — see below |

**Generation quality (level 3).** The golden set scores **0.950** across seven
algorithmic checks on the local model, median of nine samples. One check fails:
verbatim copying from the source, in English only. Two prompt fixes were tried
and measured; neither helped. The remaining gap is a 1.7B model, not the prompt.

The *spread* matters more than the median, and is why the human gate is
load-bearing: individual generations have written the Arabic post entirely in
English, and overrun X's hard limit by 37%. Two runs of an identical prompt
scored 0.976 and 0.821 — which is why the eval defaults to nine samples.

**Unattended operation (level 4).** Phase 6's own bar is *"runs for two weeks
without intervention."* Everything that stretch would exercise is built and
tested. **The fortnight has not elapsed.** That is a claim only time can support,
so it is not being made.

### Not implemented

- **Automated publishing.** The engine *records* that content went live; it does
  not post ([ADR 0018](docs/adr/0018-record-publication-do-not-perform-it.md)).
- **Facebook** as a channel — planned, and a config edit rather than code.
- **Instagram Reels / video** — planned, and genuinely new infrastructure.
- **HTTP API, web UI, authentication, billing** — Phase 7, not started.
- **Postgres** — the schema is restricted to a Postgres-compatible subset, but
  SQLite is what runs today.

---

## Architecture

Dependencies point inward only, and the rule is **machine-enforced** — not a
diagram someone drew once:

```
engine.cli            composition root, user interface
engine.pipeline       stage orchestration, run ledger, cost ceiling
engine.adapters       persistence · config · sources · LLM · rendering — all I/O
engine.observability  structured logging
engine.settings       configuration
engine.core           pure domain: entities, ports, policies (zero I/O)
```

```bash
lint-imports     # fails the build if any layer boundary is violated
```

`engine.core` may not import SQLAlchemy, Typer, httpx or any model SDK. That
constraint is what lets the same domain be driven by a CLI today and an HTTP API
later without modification.

**Everything vocabulary-shaped is data.** Channels, products, taxonomy and
ranking signals live in a versioned *domain pack*
([ADR 0015](docs/adr/0015-open-vocabularies.md)). Adding a channel is a YAML
edit. Changing the entire subject — AI news to cybersecurity — is one key in a
config file. Fetchers, parsers and ranking signals are discovered through
entry points ([ADR 0017](docs/adr/0017-plugin-discovery.md)), so extending the
engine is a package install rather than a fork.

**20 architecture decision records** in [`docs/adr/`](docs/adr/) explain the
trade-offs, including the ones that were reversed.

---

## Quickstart

Requires **Python 3.12+**. For the free local model, install
[Ollama](https://ollama.com) and `ollama pull qwen3:1.7b`.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # macOS / Linux

pip install -e ".[dev]"
copy .env.example .env            # defaults work as-is

engine db upgrade                 # create the schema
engine workspace seed             # load config/workspaces/default.yaml
engine doctor                     # verify the environment
```

RSS and the JSON APIs work immediately with no credentials:

```bash
engine schedule run               # the whole daily pipeline, idempotent
engine review queue               # what it wrote, awaiting your decision
engine review show <asset-id>     # read one in full
engine render run                 # today's edition as files
```

Gmail needs a one-time consent: `engine sources auth`
(see [`docs/INGESTION.md`](docs/INGESTION.md)).

**Verify any claim on this page yourself:**
[`docs/VERIFYING.md`](docs/VERIFYING.md).

---

## Documentation

| Doc | |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layers, domain model, extension points |
| [ROADMAP.md](docs/ROADMAP.md) | Every phase, what is done, what is planned |
| [VERIFYING.md](docs/VERIFYING.md) | How to check all of this without trusting it |
| [OPERATIONS.md](docs/OPERATIONS.md) | Running it on a timer; knowing it still works |
| [GENERATION.md](docs/GENERATION.md) | Prompt design, versioning, token budgets |
| [EVALS.md](docs/EVALS.md) | How quality is measured, and what the checks miss |
| [SCORING.md](docs/SCORING.md) · [EVIDENCE.md](docs/EVIDENCE.md) | Ranking and provenance |
| [ENRICHMENT.md](docs/ENRICHMENT.md) · [PARSING.md](docs/PARSING.md) | Sources in, stories out |
| [REVIEW.md](docs/REVIEW.md) · [RENDERING.md](docs/RENDERING.md) | The human gate and the output |
| [PLUGINS.md](docs/PLUGINS.md) | Adding a fetcher, parser or ranking signal |

---

## Engineering practices

Not decoration — each catches a class of failure that actually occurred here.

```bash
pytest                        # ~1,200 tests, no network, no model required
mypy                          # strict, across src, tests and scripts
ruff check src tests
lint-imports                  # the architecture itself
python scripts/check_migration_drift.py   # models and migrations agree
engine eval run --repeat 9    # generation quality, algorithmically
```

All of it runs in CI on every push, with an 85% coverage floor on pull requests.

Three practices are load-bearing:

- **Prompt versioning.** Every generated revision records which prompt wrote it.
  "Output got worse last Tuesday" is unanswerable otherwise. The changelog in
  `prompts.py` includes two versions that were tried, measured, and **reverted**.
- **Algorithmic evals.** No model judges another model — an LLM-as-judge costs a
  call per generation per run, which is exactly the spend that makes a suite too
  expensive to run, and a suite nobody runs is worse than none.
- **Append-only history.** Revisions, signals, publications and metrics are never
  overwritten. The difference between the model's draft and the human's rewrite
  is the training signal; overwriting would discard it silently.

---

## Editorial and legal position

Newsletters are **tips, not text**. They say *what happened*; the primary source
says *what to write*. Every claim traces to a primary source in the provenance
ledger, quotes are capped at 25 words in the domain model rather than by
convention, and content that adds nothing to someone who read the original does
not ship.

`robots.txt` is obeyed and requests are paced per host — implemented at the
transport port, so every fetcher inherits it
([ADR 0019](docs/adr/0019-politeness-as-transport-policy.md)).

---

## Licence

Unlicensed / all rights reserved. Published for portfolio and review.
