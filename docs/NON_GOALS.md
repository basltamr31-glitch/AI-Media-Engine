# Non-goals

Things this project deliberately does **not** build, and why. Kept because a
list of well-argued refusals says more about engineering judgment than a list of
features.

Each entry states the trigger that would change the decision.

---

## Architecture

**A microservice split.**
One deployable, with layers enforced by `lint-imports` instead of by network
boundaries. Distributed systems buy independent scaling and pay in operational
complexity, partial failure and distributed debugging. At current scale we would
pay in full and receive nothing.
*Trigger: a stage whose resource profile genuinely diverges — GPU inference, or
sustained fan-out that starves the rest of the pipeline.*

**A graph database.**
The entity graph is hundreds of nodes. Postgres recursive CTEs traverse that
without noticing. A second datastore means a second backup, migration and
operational story.
*Trigger: traversal depth beyond 3 hops becoming a query-latency problem, or
entity count past ~10⁵.*

**An async pipeline.**
Ingestion runs a handful of requests on a schedule. The bottleneck is an LLM call
in a later phase, not local I/O. `async` would add concurrency complexity to code
that does not need it.
*Trigger: a single run exceeding its schedule window because of I/O wait.*

**A message queue between stages.**
The runner executes stages in-process, in order, recording each. Queues buy
retry isolation and horizontal scale; they cost a broker, poison-message
handling and much harder local testing.
*Trigger: multi-tenant runs contending for the same process, or a stage that
needs independent retry semantics.*

---

## Product

**Auto-publishing without human approval.**
Enforced as of Phase 5, and stronger than the heading suggests: the engine holds
no platform credentials and has no code path that posts anywhere (ADR 0018). It
records that a human published something.
Every generated asset passes through `APPROVED` before it can be published. The
first fabricated claim published under a customer's name is a churn event, and
possibly a legal one. Human review is also the mechanism that collects the
highest-value training data (ADR 0013), so removing it would cost the moat.
*Trigger: measured approval rate above ~95% sustained for a quarter, and even
then only as an opt-in per workspace.*

**Republishing newsletter text.**
ADR 0006. Newsletters are tips, not text. We learn *what happened* from an
aggregator and *what to say* from the primary source.
*Trigger: none. This is a permanent constraint.*

**An LLM in the deduplication path.**
Deterministic scoring, with model-based adjudication reserved for a narrow
ambiguity band. Pairwise LLM comparison scales quadratically in cost and makes
clustering non-reproducible.
*Trigger: measured recall below 0.85 on the golden set, and only for the band.*

**A general-purpose chat interface.**
Every AI product grows a chat box. It is a support surface, not a product, and it
competes with tools that do it better. The engine is unattended by design.
*Trigger: repeated customer requests for ad-hoc querying that reports cannot
serve.*

**Image generation.**
Image *prompts* are produced; images are not. Generation is a commodity, the
providers change quarterly, and the cost is per-asset.
*Trigger: a destination that requires an image and cannot accept a prompt.*

---

## Platform

**Billing integration.**
Usage is metered from Phase 1.5; invoicing is not built. Metering must precede
monetisation, but a payment provider before a paying customer is speculative.
*Trigger: a signed customer.*

**Authentication and authorisation.**
`Actor` and `Membership` exist as models so audit and per-editor learning work.
Sessions, passwords and permission checks arrive with the HTTP API.
*Trigger: Phase 7, or a second human operator.*

**An HTTP API.**
The CLI is the only entry point. The composition root is deliberately thin so an
API becomes a second entry point rather than a second application.
*Trigger: Phase 7, or a customer integration that cannot be served by export.*

**A plugin marketplace.**
Entry-point discovery lets third-party packages self-register (ADR 0017). A
registry, review process and monetisation split are a business, not a feature.
*Trigger: a third-party plugin existing that we did not write.*

**A web UI.**
The review workflow is a CLI queue. A UI is the correct interface for review at
volume and is the natural first thing to build once someone other than the
author uses the system daily.
*Trigger: a non-technical operator.*

---

## Scope

**Speculative domain packs.**
`ai-news` is the only pack. Building a second one before a customer asks would be
the same over-generalisation this document exists to prevent. Vertical-agnosticism
is proven by a *synthetic* pack in the test suite, not by shipping unused ones.
*Trigger: a named second vertical with a buyer.*

**Sources nobody has asked for.**
Adding a JSON source is configuration. Adding a transport is code. YouTube,
podcasts and PDFs each need a transport and none has a current use.
*Trigger: a source that a real workflow depends on.*

**More than three channels.**
Instagram, X and LinkedIn. Breadth before quality is how content products become
mediocre everywhere.
*Trigger: a customer whose primary channel is missing.*
