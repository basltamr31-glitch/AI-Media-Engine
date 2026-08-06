# Strategy

Why this system is shaped the way it is, in business terms. The ADRs explain
individual decisions; this document explains what they are collectively trying
to build.

Written at the end of Phase 1, before Phase 1.5.

---

## What we are building

Not an AI news summariser. An **evidence-backed intelligence engine**: a
reusable platform that turns raw information into published, measured,
improving content — for any subject domain, any set of sources, any set of
channels, any brand voice.

```
Raw information → Evidence → Knowledge graph → Story → Editorial evaluation
    → Content → Multi-channel publishing → Analytics → Learning → Better stories
```

The engine contains no subject-matter knowledge. AI news is the **first
customer of our own platform**, delivered as a *domain pack* (see ADR 0014).

---

## The three-customer test

Every architectural decision is checked against three buyers who must be served
by one codebase:

| | Customer A | Customer B | Customer C |
|---|---|---|---|
| **Who** | Startup founder | Research lab | Marketing agency |
| **Wants** | Daily LinkedIn posts | Literature monitoring | 80 brands, 4 platforms |
| **Output** | Social posts | Digests, alerts, CSV | Posts at volume |
| **Values** | Voice, time saved | Coverage, citations | Leverage, consistency |
| **Price** | $50/mo | $200–500/mo | $500–2,000/mo |

Customer B is the reason "social post" cannot be the terminal abstraction
(ADR 0015). Customer C is the reason `Account` must sit above `Workspace`
(ADR 0011).

---

## The moat

Ranked by how hard each is to copy.

| # | Asset | Why it compounds | Starts filling |
|---|---|---|---|
| 1 | **Edit deltas** — what the model wrote vs what the human published | Private, behavioural, per-voice. Cannot be bought or prompted | Phase 3 |
| 2 | **Rejection reasons** | Encodes editorial taste no prompt captures | Phase 2 |
| 3 | **Engagement × content features** | *Your* audience, not a general one | Phase 4 |
| 4 | **Source reliability over time** | Who breaks stories first. Hard to reconstruct retroactively | Phase 2 |
| 5 | **Evidence database** | Citation chain from every claim to a primary source | Phase 2 |
| 6 | **Entity graph history** | Enables trend detection; independently sellable | Phase 2 |

**This moat is weak at month 1 and strong at month 12.** That asymmetry is the
entire argument for Phase 1.5: a moat that only exists in the future must start
filling in the present. Data is not backfillable — you cannot reconstruct which
headline a human would have chosen last March.

### Why not just use the obvious alternatives

| Alternative | What it does | The gap it leaves |
|---|---|---|
| ChatGPT / Claude | Write well, once, on request | No memory of your sources, rejections or performance. Cannot run unattended. Every session starts from zero |
| Perplexity | Answer questions with citations | Answers questions; is not a publishing pipeline |
| Feedly | Find and filter | Does not write, does not learn a voice, does not publish |
| Buffer / Hootsuite | Schedule and publish | Schedules what you already wrote. No editorial layer |
| Zapier / n8n | Connect anything to anything | No judgment. Ships whatever the model emitted, unranked and unreviewed |

Each owns one stage. Nobody owns the **accountable, learning, unattended
editorial loop**: find → judge → write in your voice → get approved → publish →
measure → improve.

---

## Domain packs: the business model this architecture implies

The engine is vertical-agnostic. A **domain pack** is a versioned bundle of
configuration — entity types and gazetteer, taxonomy, ranking signals and
weights, channel definitions, output products, prompt templates — that turns the
generic engine into a specific product.

```
Engine (code, one repository)      Domain Pack (data, one bundle)
────────────────────────────       ──────────────────────────────
transports: email / rss / api      entity types + gazetteer
stage contract + runner            taxonomy
similarity algorithm               ranking signals + weights
ranking framework                  channel definitions
generation framework               output-product definitions
review workflow                    prompt templates
ledger, evidence, provenance       brand voices
```

Commercially, each pack is a SKU with its own price anchor:

| Pack | Buyer | Anchor | Incumbent they already pay |
|---|---|---|---|
| AI News | Marketers, agencies | $50–500/mo | Feedly + Buffer + ChatGPT |
| Cybersecurity | SOC teams, MSSPs | $500–2,000/mo | Recorded Future ($10k+) |
| Pharma / clinical | CI teams, biotech | $1,000–5,000/mo | Cortellis, Evaluate ($20k+) |
| Legal / regulatory | Compliance | $500–2,000/mo | Lexology, Law360 |
| Finance | IR teams, funds | $500–5,000/mo | AlphaSense ($20k+) |

The engineering cost of pack #2 is a YAML bundle and a gazetteer. The revenue
multiple is 10–40×.

**Provenance is what unlocks the expensive verticals.** ADR 0006 introduced
primary-source linking for legal reasons. It turns out that a citation chain
from every published claim to a timestamped primary source is a *compliance
requirement* in pharma, legal and finance — and the reason incumbents charge
$20k/year. We already built it.

---

## The learning loops

A static pipeline is a script. These are the loops that make it a product.

| Loop | Signal captured | What it improves | Earliest |
|---|---|---|---|
| Source quality | selected ÷ ingested per source | `trust_weight` auto-tuning | Phase 2 |
| Dedup | human "same / not same" correction | similarity thresholds | Phase 2 |
| Editorial fit | story approved / rejected + reason | ranking weights | Phase 3 |
| **Style** | **draft ↔ published diff** | few-shot exemplars in brand profile | Phase 3 |
| Headline | variant → engagement | headline generation | Phase 4 |
| Timing | publish time → engagement | schedule slots | Phase 4 |
| Topic | topic → engagement | `audience_fit` weight | Phase 5 |

The style loop is the one to obsess over. Every edit a customer makes is a
labelled preference pair *in their own voice*. After a few hundred edits the
output stops needing editing — and that is precisely what a general model cannot
provide, because it does not remember what this customer fixed last Tuesday.

All seven loops write to one append-only `signals` table (ADR 0012). One table,
not seven, so a new loop needs no migration.

---

## Cost discipline

> Never design something that scales linearly in LLM cost if an algorithm can
> solve it.

| Concern | Design |
|---|---|
| Deduplication | Deterministic: URL canonicalisation, lexical similarity, entity overlap. **Zero LLM** |
| Entity resolution | Gazetteer lookup. Zero LLM |
| Classification | Rules from the pack. Zero LLM |
| Enrichment | Only for the ranked shortlist — ~8 fetches, not ~60 |
| Signals | Cached per `(story, signal, version)` |
| LLM adjudication | Reserved for the narrow similarity ambiguity band; single-digit pairs per day |

**Phase 2 makes zero LLM calls and costs $0/day to run.** That is not asceticism:
it is the deterministic baseline that makes any later model addition
*measurable* rather than assumed. The eval harness (`engine evals dedup`) is what
converts "should we add embeddings?" from an opinion into a number.

---

## Where the value accrues, by phase

| Phase | Engineering output | Commercial asset created |
|---|---|---|
| 0 | Foundation | Multi-tenancy, cost accounting, provenance model |
| 1 | Ingestion | Raw archive — replayable history from day one |
| **1.5** | **Foundations for capture** | **Signal log, revision chain, pack abstraction** |
| 2 | Intelligence | Evidence DB, entity graph, source reliability |
| 3 | Generation + review | **Edit deltas** — the strongest moat asset |
| 4 | Products, destinations, scheduling | Engagement data |
| 5 | Learning | Fitted ranking, adapted style |
| 6 | Automation, onboarding | Self-serve; source discovery |
| 7 | API, billing, quotas | Recurring revenue |
| 8 | Plugin ecosystem | Third-party sources and destinations |

Note the change from the original roadmap: **review moved from Phase 5 into
Phase 3**, because review *is* the data-collection mechanism for the highest-value
asset. Collecting it two phases later would cost a year of edit deltas.

---

## What would falsify this strategy

Stated plainly, so it can be checked rather than assumed:

- If dedup precision cannot clear 0.90 on real data, the "evidence-backed" claim
  is hollow and Phase 2 must be redesigned before Phase 3.
- If generated content is edited beyond recognition every time, the style loop
  has no signal and voice is not a defensible feature.
- If no second domain pack can be built without touching engine code, the
  vertical-agnosticism claim is false and the pack abstraction failed.
- If a customer will not pay $50/month after 90 days of accumulated data, the
  moat thesis is wrong and the product is a productivity toy.

Each has a checkpoint in the roadmap. None is assumed to be true.
