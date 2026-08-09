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
impression. On the shipped local model (`qwen3:1.7b`, median of **nine**
samples) the suite scores **0.950** across seven checks, with one open failure: verbatim copying
from the source on English LinkedIn. Arabic never fails that check, because
translating makes copying impossible — the failure is English-only and is a
limit of a 1.7B model rather than of the prompt.

That 0.950 is **not** an improvement on the 0.941 recorded before
`no_fused_words` existed. Adding a seventh check that the golden cases pass
raises the mean by arithmetic; the two numbers are computed over different
denominators and are not comparable. The golden set never contained a fused
word — the failure was found by reading real output, which is the argument for
doing that as well as running the suite.

Nine samples rather than three, learned the hard way: two runs of an identical
prompt scored 0.976 and 0.821, so a three-sample comparison cannot tell a
prompt improvement from a lucky draw. The tail is the operational risk — single
generations have written the Arabic post in English and overrun X's limit by
37% — and it is why the human review gate is load-bearing rather than
ceremonial.

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

## Phase 5 — Review, distribution, measurement ✅

- Review queue and approve/reject/edit workflow over the content state machine
- Channel export formats, applying the pack's per-channel rules
- `Publication` recording
- Manual `PerformanceMetric` entry
- First engagement report

**Done when:** the loop is closed — content published, performance recorded,
data available to ranking. ✅

The loop runs end to end: a draft is reviewed, edited, approved, exported as the
exact text to paste, recorded as published with a URL, and measured. Every step
emits a signal attributed to a real actor, so `content.edited`,
`content.approved`, `content.rejected`, `content.published` and
`publication.engagement` are populated for the first time rather than merely
declared.

Two decisions are worth recording.

**The engine records publication rather than performing it**
([ADR 0018](adr/0018-record-publication-do-not-perform-it.md)). Auto-publishing
needs per-platform OAuth, a scheduler and a retry queue, and it closes nothing
until the last integration lands — while producing exactly the `Publication`
rows the manual path produces today. What the feedback loop needs is the *link*
between an asset and an outcome; who pressed *post* is not the interesting part.
The cost is a manual last mile, accepted; the benefit is that the loop is
complete now, for every channel including those with no API, with no platform
credentials anywhere in the system.

**A human edit is measured, not just stored.** The revision chain already kept
the model's draft beside the human's rewrite; Phase 5 adds
`similarity`/`characters_changed`/`is_rewrite` so the pair is queryable in
aggregate. The comparison is character-level rather than word-level because
Arabic marks agreement and definiteness with affixes: a one-letter correction
changes the inside of a word, and a word-level ratio would report a rewrite
where an editor would say a diacritic was added.

One defect from Phase 3.4 was fixed here rather than carried: generated
revisions were attributed to a hard-coded `"actor_system"` that matched no row
in `actors`. It read correctly in every test that only read it back, and joined
to nothing. Attribution now resolves the account's real system actor, and a
human `owner` actor is provisioned alongside it so review decisions attribute to
a person.

Nothing consumes the engagement data yet. Refitting ranking weights against it
is Phase 6; the schema was built for it
([ADR 0016](adr/0016-open-scoring-profile.md)), but no learner exists.

See [`docs/REVIEW.md`](REVIEW.md).

---

## Phase 6 — Hardening ✅ *(capability complete; see the caveat below)*

- ✅ **Politeness** — `robots.txt` and per-host rate limiting, as a decorator
  over the HTTP port so every fetcher inherits them
  ([ADR 0019](adr/0019-politeness-as-transport-policy.md)). This was the
  precondition for scheduling, not an afterthought to it.
- ✅ **The daily pipeline actually assembled.** `pipeline run` had never been
  updated past Phase 1 and ran a single ingest stage; it now runs all eight,
  stopping before rendering so unattended work ends by filling the review queue.
- ✅ **Scheduling** — `engine schedule run`, idempotent, with a stale-tolerant
  lock so two runs cannot overlap, and distinct exit codes for "failed" and
  "skipped because busy".
- ✅ **Retries and failure alerting** — retries and exponential backoff already
  existed; Phase 6 adds alert sinks (log and `data/alerts.jsonl`) and
  `engine schedule health`, which reports **staleness** — the failure that
  produces no failed runs to notice.
- ✅ **Feedback-driven weight tuning** — `engine tune weights` correlates each
  ranking signal against measured engagement and proposes a profile. It
  proposes and never applies
  ([ADR 0020](adr/0020-weight-tuning-proposes-never-applies.md)).
- ✅ **CI** — GitHub Actions running format, lint, mypy, import contracts,
  tests, a migration-drift check, and an 85% coverage floor.
- ⏸ **Postgres** — deferred, per this phase's own condition: *"when concurrency
  demands it"*. It does not. The schema is already restricted to a
  Postgres-compatible subset, so this remains a connection-string change.

**Done when:** it runs unattended for two weeks without intervention.

**Honest status:** the capability is complete and tested, and the two-week
unattended run **has not happened yet** — it is a claim that only elapsed time
can support. Everything that stretch would exercise is in place; nothing has
yet proved it survives fourteen consecutive mornings.

One further limit worth stating: the golden-set quality floor is enforced by
`engine eval run --min-score`, but that job cannot run on a hosted CI runner,
which has no model. It runs locally, or on a self-hosted runner with the model
resident. The CI workflow declares the job and skips it visibly rather than
pretending the gate is enforced everywhere.

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
