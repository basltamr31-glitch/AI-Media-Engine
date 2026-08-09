# Project context

Orientation for anyone — human or AI assistant — picking this repository up
cold. `README.md` is the shop window; this is the map. `CLAUDE.md` is the
working policy and takes precedence over anything here.

---

## What this is

An **AI Media Engine**: multi-source ingestion → editorial ranking →
multi-channel content generation, with a human approval gate and a measured
feedback loop.

The framing that matters: **AI news is the first *domain*, not the product.**
Every subject-specific fact — taxonomy, channels, products, ranking signals —
lives in a versioned *domain pack* under `packs/`. Switching to cybersecurity or
finance is a config key, not a fork. Anything that hard-codes "AI news" into the
engine is a bug.

Three things the architecture exists to protect, from
`docs/ARCHITECTURE.md`:

1. **Editorial judgement** — choosing 3 stories from 40 is the durable asset,
   so ranking is explainable, tunable and stored as data.
2. **Verifiable provenance** — every claim traces to a primary source.
3. **The feedback loop** — publish → engagement → ranking, instrumented from the
   start, because it cannot be backfilled.

---

## Layout

```
src/engine/
  core/          pure domain — entities, ports, policies. ZERO I/O.
  adapters/      all I/O: persistence, sources, LLM, rendering, http, alerts
  pipeline/      stages + runner + run ledger
  observability/ structured logging
  settings.py    configuration (pydantic-settings)
  cli/           composition root; also the application layer (ADR 0002)
config/          workspaces and brand profiles — policy as data
packs/           domain packs: taxonomy, channels, products, ranking signals
evals/           golden cases + the recorded baseline
migrations/      Alembic
docs/adr/        20 architecture decision records
```

**Dependency rule, machine-enforced by `lint-imports`:** cli → pipeline →
adapters → observability → settings → core. Inward only. `engine.core` may not
import SQLAlchemy, Typer, httpx or any model SDK.

---

## The nine stages

`ingest → parse → resolve → enrich → evidence → score → select → generate →
render`, plus `export`. `engine schedule run` executes the first eight and stops
before rendering, so unattended work ends by filling the review queue rather
than declaring a day finished that nobody read.

Every capability is a **stage** implementing `Stage`, never a new pipeline.
Stages get the run ledger, cost accounting and observability for free.

---

## Non-negotiable rules

These are enforced in code and have each been violated at least once:

- **Newsletters are tips, not text** (ADR 0003). Generation reads the *extracted
  primary source*, never a newsletter's summary. This is what stops the engine
  being a republisher.
- **Never scale LLM cost linearly where an algorithm will do.** The eval suite is
  entirely algorithmic for this reason; no model judges another model.
- **Append-only history.** Revisions, signals, publications and metrics are never
  updated in place. The generated-vs-edited difference is the training signal.
- **Every action is attributed to a real actor row.** Automated work uses the
  account's `system` actor; human decisions use a human actor. A fabricated id
  looks fine and joins to nothing.
- **Cost is recorded per revision**, not per asset. Regenerating spends again,
  and a per-asset total hides retries.
- **Quotes capped at 25 words**, in the domain model rather than by convention.

---

## Current state (end of Phase 6)

Phases 0–6 complete: foundation, ingestion, vertical-agnostic packs, extraction
and enrichment, editorial and generation, rendering, review and measurement,
hardening.

Verified: ~1,200 tests, strict mypy across `src`/`tests`/`scripts`, ruff clean,
two import contracts kept, migrations match the models, single Alembic head.

**Two gaps stated plainly, and they must not be silently upgraded:**

1. **Quality.** Golden set at **0.950**, median of nine samples, on `qwen3:1.7b`.
   One open failure: English-only verbatim copying. The variance is the real
   risk — single generations have produced 0% Arabic in an Arabic post.
2. **Unattended.** Phase 6's bar is two weeks without intervention. Built and
   tested; **the fortnight has not elapsed.**

Nothing has been published to a real platform. The engagement log is empty and
truthful — a smoke test once left a fabricated publication in it, and removing
that is recorded in the git history.

---

## Where the interesting decisions live

| Question | Answer |
|---|---|
| Why one package, not four? | ADR 0002 |
| Why is publication *recorded*, not performed? | ADR 0018 |
| Why are channels data instead of an enum? | ADR 0015 |
| Why does weight tuning only propose? | ADR 0020 |
| Why is robots.txt at the transport port? | ADR 0019 |
| Why does the revision chain never overwrite? | ADR 0013 |
| Why nine eval samples, not three? | `docs/VERIFYING.md` |

---

## Known sharp edges

- **Windows**: do not write non-ASCII files with PowerShell `Set-Content` — it
  double-encodes. This corrupted `README.md` once. Use editor tooling.
- **Alembic**: autogenerate emits `astext_type=Text()` unqualified for JSONB
  variants, which raises `NameError`. Correct it to `sa.Text()`.
- **Alembic**: a `NOT NULL` column on a populated table needs a `server_default`
  declared on *both* the migration and the model, or every later autogenerate
  reports phantom drift.
- **`anthropic` is an optional extra.** Run tooling inside `.venv`. An
  environment with only `.[dev]` skips the Anthropic tests — correct behaviour,
  and the reason the test count moves.
- **Generation is non-deterministic.** Use `--repeat 9` for any comparison.
  Three samples cannot separate a real change from a lucky draw.

---

## Planned next (not built)

- **Phase 6.5 — canonical English.** Today each language is generated
  *independently*, so the Arabic and English posts about one paper say different
  things. The target is: English is written and approved, other languages are
  **translations of the approved English**, and adding a language is config.
- **Phase 7 — platform.** HTTP API, auth, billing, web UI, automated publishing.
  Blocked more by platform approvals (Meta App Review, LinkedIn Marketing
  Developer Platform, X's paid tier) than by architecture.
- **Backlog:** Facebook channel (config-sized), more sources (config or one
  plugin), Instagram Reels (genuinely large — video rendering, Arabic text
  shaping in video, media storage).

See `docs/ROADMAP.md` for the full picture with costs attached.
