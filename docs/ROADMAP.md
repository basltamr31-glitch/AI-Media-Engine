# Roadmap

Phases are approved and started one at a time. Each ends production-quality:
documented, tested, modular. No phase begins before the previous one is approved.

---

## Phase 0 — Foundation ✅

Domain model, persistence with migrations, policy-as-data configuration,
pipeline orchestration with run ledger and cost ceiling, structured logging,
CLI, test suite, machine-enforced architecture.

**Done when:** `engine doctor` passes, `engine pipeline run --smoke` executes
end to end, all checks green. ✅

---

## Phase 1 — Ingestion ✅

Turn configured sources into stored `SourceDocument` records.

- `SourceFetcher` implementations: Gmail (email), HTTP API, RSS
- Gmail OAuth device flow; read-only scope
- Idempotency via content hash; local state so re-runs are safe
- `ingest` stage wired into the runner
- Raw document archival for replay
- `engine ingest run` / `engine ingest status`

**Done when:** a real run fetches from all three sources, stores them, and a
second run correctly ingests nothing new. ✅

**Explicitly not in Phase 1:** parsing, story extraction, marking mail as read.

---

## Phase 1.5 — Vertical-agnostic foundations ✅

Inserted between Phases 1 and 2, before any parser existed. The trigger was
recognising that the engine was AI-news-shaped in places it did not need to be:
a `Channel` enum, a `ContentKind` enum and a two-member `Language` enum each
made a second vertical a code change rather than a configuration change. Fixing
that after Phase 2 would have meant migrating stored data; fixing it first cost
nothing.

- Account and Actor above Workspace, so "who acted" is recordable (ADR 0011)
- One append-only `Signal` log rather than a table per feedback type (ADR 0012)
- `ContentRevision` chain, preserving the draft-to-published diff (ADR 0013)
- Domain packs: taxonomy and vocabulary as versioned data (ADR 0014)
- Open vocabularies — `ChannelDef`, `ProductDef` and validated BCP-47 language
  tags replace the three enums; `config/channel_specs/` folds into the pack
  (ADR 0015)
- Open `ScoringProfile`: ranking signals declared by the pack, weights an open
  map validated against them (ADR 0016)
- Plugin discovery through entry points, so the engine's own fetchers and
  parsers register exactly as a third-party package would (ADR 0017)
- `engine pack list` / `show` / `validate`, `engine plugins list`

**Done when:** swapping the active pack changes what subject the engine is
about, with no code change. ✅

**Explicitly not in Phase 1.5:** any new pipeline capability. This phase moved
knowledge out of code; it added no stage.

---

## Phase 2 — Extraction, resolution, enrichment ✅

The layer where quality is won.

- `DocumentParser` implementations: TLDR, Hacker News, arXiv
- Ad, sponsor and promo removal
- `StoryCandidate` extraction with outbound links
- Cross-source clustering into canonical `Story`
- **Primary-source enrichment** — fetch and extract the actual paper/blog/release
- `Evidence` records binding claims to sources
- Test fixtures from real captured documents

**Done when:** one morning's inputs produce a clean, deduplicated story list
where every story carries primary-source content. ✅

Shipped in five milestones: parse stage and candidate storage with the TLDR
parser (2.1); arXiv and Hacker News parsers (2.2); cross-source clustering into
canonical stories (2.3); primary-source fetching and extraction (2.4); the
provenance ledger (2.5).

Measured on the first real corpus: 137 documents produced 546 candidates and
523 stories, of which 21 were reported more than once and 5 by more than one
source. Clustering thresholds were calibrated against that corpus rather than
guessed — see the `resolve` stage docstring.

One honest caveat on "every story carries primary-source content": the
capability is complete, but coverage is an operational matter. Enrichment is
rate-limited by design (25 stories per run, one request at a time), so a
backlog is worked through over several runs, and some links — paywalls,
link-shorteners that refuse non-browser clients — are permanently unreadable.
`engine enrich status` reports the gap and its reasons.

---

## Phase 3 — Editorial and generation ✅

- Explainable scoring against the workspace `ScoringProfile`; `EditorialScore` with components
- Selection honouring `minimum_score` and `require_primary_source`
- `LLMPort` implementation, structured outputs, prompt caching — local (Ollama)
  provider on the development path, Anthropic written and wired behind
  configuration for when quality justifies paying for it
- Generation: brand voice × channel × language → `ContentAsset`
- Cost tracking per revision (the unit of spend); ceiling enforced before each call
- Golden-set evals so quality regressions are visible — algorithmic checks only,
  so the suite costs nothing to run after every prompt change

**Done when:** output is good enough to publish unedited.

The *capability* is complete and verified end to end against a real model: a
selected story becomes one post per brand voice, channel and language, priced
per generation, with a ceiling enforced before each call.

The *bar* is not met yet, and the golden set is what says so rather than an
impression. On the shipped local model (`qwen3:1.7b`, median of three samples)
the suite scores **0.881**, with two open failures: verbatim copying from the
source, and writing past X's 280-character limit. Both are limits of a 1.7B
model rather than of the prompt — the same suite scores every case clean on
well-formed input.

That gap is deliberate rather than deferred. Generation quality had to become
*measurable* before it was worth paying for, which is why the local provider
came first; switching to a hosted model (`ENGINE_LLM_PROVIDER=anthropic`, no
code change) now moves a number instead of an opinion. Phase 4 does not depend
on closing it: rendering a draft and improving a draft are independent.

---

## Phase 4 — Rendering ✅

- `package.json` — canonical machine output, self-contained and deterministic
- `post.md` — human-readable, all channels and languages, derived from the
  package so the two cannot disagree
- Dated output directories, in the workspace's timezone rather than UTC
- Arabic RTL rendering: each Arabic paragraph carries an explicit
  `dir="rtl"` rather than relying on a renderer's inference

**Done when:** a daily folder is directly usable for publishing.

The RTL decision is the one worth recording. Markdown cannot state text
direction, so renderers infer it from Unicode UAX#9 rules P2/P3 — the first
strong character wins. That inference is wrong precisely for the sentences this
engine produces: an Arabic paragraph opening with a Latin product name
(`MatrAIx يُقدم…`) is laid out left-to-right, moving the full stop to the wrong
side. It is unreadable to an Arabic reader and looks fine to everyone else, so
it ships. A test computes P2/P3 directly and pins that the emitted direction no
longer depends on which word the model happened to start with.

---

## Phase 5 — Review, distribution, measurement

- Review queue and approve/reject workflow over the content state machine
- Channel export formats
- `Publication` recording
- Manual `PerformanceMetric` entry
- First engagement report

**Done when:** the loop is closed — content published, performance recorded,
data available to ranking.

---

## Phase 6 — Hardening

- Feedback-driven weight tuning
- Scheduling
- Retries, rate limiting, failure alerting
- Postgres migration when concurrency demands it
- Coverage and eval thresholds in CI

**Done when:** it runs unattended for two weeks without intervention.

---

## Phase 7 — Platform

- HTTP API over the same domain
- Multi-workspace onboarding, authentication, billing
- Web review UI
- Scheduled publishing

**Done when:** a customer can be onboarded without the founder in the loop.

---

## Business milestones these serve

| Window | Goal |
|---|---|
| M0–1 | Engine produces publishable output |
| M1–3 | 60 consecutive days published |
| M3–6 | Feedback loop measurably improving engagement |
| M6–9 | First revenue: sponsorship or paid tier |
| M9–15 | Done-for-you service, 5–10 clients |
| M15–24 | Self-serve platform |

**Kill criterion:** if by month 6 publishing is not consistent or the audience is
not growing, the product is not the problem. Stop and reassess rather than
adding features.
