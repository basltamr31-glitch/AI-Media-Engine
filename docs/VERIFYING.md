# How to check this yourself

Every claim in this repository is meant to be one command away from being
checked. This page is the list of those commands, in the order that builds
confidence fastest: does it stand up, does it hold together, does it produce
anything, and is what it produces any good.

Nothing here costs money. Every command runs against the local model and the
local database.

---

## 1. Does it stand up?

```bash
engine doctor
```

Twelve checks in one table: Python, paths, database, migrations, config, packs,
the model, fetchers, plugins, credentials. Anything not `OK` names its own fix.

The row that matters most is **Migrations** — `up to date (179e96806dd8)` means
the database schema and the code agree. If they disagree, everything below is
measuring the wrong thing.

---

## 2. Does it hold together?

These are the checks that must pass before any work is called complete. They
take about four minutes in total.

```bash
pytest                       # ~1200 tests
mypy                         # strict, 213 files
ruff check src tests         # lint
ruff format --check src tests
lint-imports                 # the architecture, machine-enforced
```

**Run these inside the project's virtualenv** (`.venv`), not a system Python.
The two differ in a way that matters: `anthropic` is an optional extra, so an
environment that installed only `.[dev]` skips `tests/unit/test_anthropic_client.py`
entirely and reports about ten fewer tests. That skip is correct — it is the
extra being genuinely optional — and it is why the exact count moves.

```
SKIPPED [1] tests/unit/test_anthropic_client.py: requires the optional 'llm' extra
```

To run those too: `pip install -e ".[llm,dev]"`, which is what CI does.

`lint-imports` is the unusual one and worth understanding: it fails if any layer
imports inward-out — if the domain touched SQLAlchemy, or an adapter imported a
pipeline stage. The layering in `docs/ARCHITECTURE.md` is a property of the
code, not a diagram someone drew once.

```bash
python scripts/check_migration_drift.py
```

Autogenerates a migration against the current models and asserts it comes back
empty. A non-empty result means a model changed and nobody wrote the migration.

Everything above also runs on every push, in `.github/workflows/ci.yml`.

---

## 3. Does the model actually work?

```bash
engine llm check
```

Sends one real request to the local model and validates the reply against a
schema. It reports the model, token counts, cost and elapsed time. This proves
the provider is reachable and that **structured output works** — the engine does
not parse prose out of a text blob, it requires conforming JSON.

Cost reads `$0.000000` because the model is local. That is not a rounding
artifact; it is the actual price.

---

## 4. Does it produce anything?

Run the whole day's work in one command:

```bash
engine schedule run
```

Idempotent — every stage skips what it has already done, so running it twice
costs almost nothing and duplicates nothing. Expect roughly 50 minutes on the
local model, nearly all of it in generation.

Then look at what happened, stage by stage:

```bash
engine pipeline runs                 # history, duration, cost
engine pipeline stages <run-id>      # in/out counts per stage
```

A healthy run reads like this — documents in, stories out, a handful selected,
and posts written for each channel and language:

| Stage | In | Out |
|---|---|---|
| ingest | 47 | 23 |
| parse | 160 | 120 |
| resolve | 120 | 103 |
| enrich | 25 | 19 |
| evidence | 200 | 202 |
| score | 200 | 103 |
| select | 626 | 3 |
| generate | 30 | 22 |

`generate` writing fewer than it was asked for is **normal and intended**: a post
over the channel's character limit is refused rather than truncated. The
breakdown says which failure it was, and each one names its own fix.

---

## 5. Is what it produced any good?

Three different questions, three different tools.

### Read it

```bash
engine review queue                  # everything awaiting a decision
engine review show <asset-id>        # one post, in full
engine review show <asset-id> --history   # every revision, and what each cost
```

This is the one that cannot be automated. The checks below catch broken output;
only a reader catches *dull* output.

### Score it

```bash
engine eval run --repeat 9
```

Generates a fixed set of golden cases and scores them against seven algorithmic
checks — language, copying, unsupported numbers, length, hook, required terms,
and words welded from two scripts. Nothing calls a model to judge another model,
so the suite is free to run after every prompt change.

**Use `--repeat 9`.** Generation is not deterministic, and this was learned the
expensive way: two runs of an *identical* prompt scored 0.976 and 0.821. Three
samples cannot distinguish a real improvement from a lucky draw. Nine can.

Current baseline: **0.950**, median of nine, on `qwen3:1.7b`.

### Read the day's edition

```bash
engine render run
```

Writes `output/<workspace>/<date>/` containing `package.json` (the canonical
machine output) and `post.md` (the human view). Open `post.md` — Arabic
paragraphs carry an explicit `dir="rtl"`, so they read correctly instead of
depending on a renderer's guess.

---

## 6. What did it cost?

```bash
engine cost report
```

Per asset, per attempt, and per recent run. Every figure is `$0.000000` on the
local model — which is the point of having built it this way. The same command
will show real numbers the day a hosted model is switched on, with no code
change, because cost is recorded per revision from the start.

---

## 7. Is it still running?

Only relevant once a scheduler is driving it.

```bash
engine schedule status     # has it been running, and did those runs pass?
engine schedule health     # the same question as an exit code, for a monitor
```

`health` exits non-zero when unhealthy, so a monitor can poll it. It reports
**staleness** — a scheduler that stopped firing produces no failed runs to
notice, which is the failure mode that ends an unattended stretch.

Dry runs do not count towards health. A rehearsal stores nothing and succeeds
reliably, so a command left on `--dry-run` would otherwise report green forever.

---

## What "done" does and does not mean

The status table in `README.md` distinguishes four things deliberately, and they
are not the same:

1. **capability implemented** — the code exists
2. **capability tested** — and it is covered
3. **quality bar achieved** — and the output is good enough
4. **production-ready** — and it has survived real use

Phases 0–6 are complete at levels 1 and 2. Two gaps are stated plainly rather
than rounded up:

- **Generation quality (level 3).** 0.950 across seven checks, with one open
  failure: English-only verbatim copying from the source. Two prompt fixes were
  tried and measured, and neither helped — the remaining gap is the 1.7B model,
  not the instruction. Arabic never fails that check, because translating makes
  copying impossible.
- **Unattended operation (level 4).** Phase 6's bar is "runs for two weeks
  without intervention". Everything that stretch would exercise is built and
  tested; the fortnight has not elapsed. That is a claim only time can support.

The variance matters more than the median, and is why the human review gate is
load-bearing: individual generations that a median hides have written the Arabic
post entirely in English, and overrun X's limit by 37%.

---

## The shortest version

If you only run five commands:

```bash
engine doctor              # it stands up
pytest                     # it holds together
engine schedule run        # it produces
engine review queue        # you read what it produced
engine eval run --repeat 9 # and the score has not moved
```
