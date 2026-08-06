# Future ideas

Ideas raised during design that are **not part of the current design** and are
not being built. Recorded so they are not lost, and so nobody re-derives them
from scratch later.

Nothing here is approved. Nothing here should influence current implementation.
An entry graduates only when a shipped feature needs it and the owner approves.

Distinct from `NON_GOALS.md`, which lists things deliberately refused. These are
things that might be good, later.

---

## Deferred platform concepts

**Decision as a first-class object.**
A record of every automated choice — merge, classify, rank, select — with its
alternatives, confidence and method, where a human override is a new decision
superseding the old. Would unify human-in-the-loop across every stage and make
overrides directly usable as training data.
*Cost of deferral:* override data is not captured; each intervention point needs
bespoke plumbing when it arrives.
*Revisit when:* a second human intervention point is genuinely required.

**Knowledge layer — claims, facts, events, timelines.**
Stories are episodic and decay; durable assertions about the world compound.
Temporal, supersedable facts would enable "what did we believe in August?",
grounded verification of generated claims, and a separately sellable knowledge
product.
*Cost of deferral:* `Evidence` stays bound to stories rather than to claims;
re-pointing it later is one foreign key.
*Revisit when:* verification of generated content becomes a requirement.

**Declarative pipelines.**
Pipeline composition as versioned data — an ordered list of stage keys with
per-stage config — so a workspace can drop or reorder stages without a deploy.
Strictly linear; never expressions or branching.
*Cost of deferral:* stage lists stay in `build_daily_stages()`. Cheap to move.
*Revisit when:* a second workspace needs a materially different stage list.

**Feature packs.**
Optional capability bundles (SEO, video, translation) alongside domain packs,
sold as add-ons. Requires namespaced keys and a fifth config resolution layer.
*Revisit when:* a capability exists that some customers want and others do not.

**Plugin maturity tiers.**
Stable / experimental / internal contracts with a written deprecation policy and
a shipped conformance test suite.
*Revisit when:* a third-party plugin exists that we did not write.

**Inbound event table.**
Append-only webhook receipts, so receipt is decoupled from processing and push
sources (Gmail push, GitHub webhooks) stop needing polling.
*Revisit when:* the first push-based source is added.

**Inference records and cache.**
Every model call recorded with model, prompt version, input hash and cost;
cached on `(task, input_hash, model_version)`. Detects silent provider
regressions and cuts repeat cost.
*Revisit when:* the first LLM call ships in Phase 3.

**Observed vs derived data marking.**
Explicit classification driving retention, backup and replay policy: observed
data is never deleted, derived data is always recomputable.
*Cost of deferral:* low — it is a naming and documentation convention before it
is a mechanism.

**Learned source reliability.**
`trust_weight` as a static config value goes stale. Splitting it into a
configured prior plus an observed reliability score would let ranking inputs
improve with use.
*Revisit when:* enough selection data exists to compute reliability — realistically
after Phase 3.

---

## Scale

Current design target is **10³–10⁴ documents/day**. Known ceilings:

| Component | Breaks around | Replacement |
|---|---|---|
| SQLite | 10⁴/day | Postgres (URL change; schema is already compatible) |
| Pairwise similarity | 10⁴/day | Blocking, then MinHash/LSH |
| One archive file per document | 10⁵/day | Object storage with batching |
| In-process synchronous stages | 10⁵/day | Queue and workers |
| Unpartitioned `signals` | 10⁷ rows | Time partitioning plus rollups |

None require redesign — each sits behind an existing port or a portable schema.
