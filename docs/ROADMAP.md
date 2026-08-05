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

## Phase 1 — Ingestion

Turn configured sources into stored `SourceDocument` records.

- `SourceFetcher` implementations: Gmail (email), HTTP API, RSS
- Gmail OAuth device flow; read-only scope
- Idempotency via content hash; local state so re-runs are safe
- `ingest` stage wired into the runner
- Raw document archival for replay
- `engine ingest run` / `engine ingest status`

**Done when:** a real run fetches from all three sources, stores them, and a
second run correctly ingests nothing new.

**Explicitly not in Phase 1:** parsing, story extraction, marking mail as read.

---

## Phase 2 — Extraction, resolution, enrichment

The layer where quality is won.

- `DocumentParser` implementations: TLDR, Hacker News, arXiv
- Ad, sponsor and promo removal
- `StoryCandidate` extraction with outbound links
- Cross-source clustering into canonical `Story`
- **Primary-source enrichment** — fetch and extract the actual paper/blog/release
- `Evidence` records binding claims to sources
- Test fixtures from real captured documents

**Done when:** one morning's inputs produce a clean, deduplicated story list
where every story carries primary-source content.

---

## Phase 3 — Editorial and generation

- Explainable scoring against `ScoringWeights`; `EditorialScore` with components
- Selection honouring `minimum_score` and `require_primary_source`
- `LLMPort` implementation (Anthropic), structured outputs, prompt caching
- Generation: brand voice × channel × language → `ContentAsset`
- Cost tracking per asset; ceiling enforced
- Golden-set evals so quality regressions are visible

**Done when:** output is good enough to publish unedited.

---

## Phase 4 — Rendering

- `package.json` — canonical machine output
- `post.md` — human-readable, all channels and languages
- Dated output directories
- Arabic RTL rendering verified in real editors

**Done when:** a daily folder is directly usable for publishing.

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
