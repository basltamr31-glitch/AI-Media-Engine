# Generation

Selected stories become text. The matrix is **brand voice × channel ×
language**: one story, in a workspace with two languages and three channels,
is six separately addressable assets.

```
selected story ─┬─ linkedin × en ─→ ContentAsset ─→ revision 1 (generated)
                ├─ linkedin × ar ─→ ContentAsset ─→ revision 1 (generated)
                ├─ x        × en ─→ ContentAsset ─→ revision 1, 2 (regenerated)
                └─ …
```

```bash
engine generate run                      # every selected story, every channel
engine generate run --channel linkedin   # one channel
engine generate run --story sty_… -n 1   # one story
engine generate list
engine generate show ast_… --history
```

## What a story must be to be written from

Two gates, both re-checked here even though selection already applied them:

1. **Selected.** Generating by `--story` id must not route around the
   editorial gates.
2. **Enriched.** The prompt is built from the *extracted primary source*, never
   from a newsletter's summary. This is where "newsletters are tips, not text"
   (ADR 0003) stops being a slogan — break it here and the engine is a
   republisher.

A story failing either is counted in the run's refusals, not skipped silently.

## The asset is the slot, not the run

An asset is identified by `(story, product, channel, language, variant)`. Run
generation twice and you do not get two rows — the existing asset gains
revision *n+1* and its `current_revision_id` moves forward.

That matters because the revision chain is the only place the difference
between **what the model wrote** and **what a human published** is kept, and
that difference is a labelled preference pair in the customer's own voice,
produced as a by-product of work they were doing anyway (ADR 0013). Overwriting
`body` would discard it silently and permanently.

`engine generate show <id> --history` prints the chain.

## Prompt versioning

Every generated revision records the `prompt_version` that produced it. Without
it, "output got worse last Tuesday" is unanswerable, because you cannot tell
whether the prompt, the model or the source changed.

It earns its keep immediately. The shipped history:

| Version | Change | Why |
|---|---|---|
| `gen-1` | First prompt. | — |
| `gen-2` | Name the language in words. | `gen-1` said "in ar". A small model read a BCP-47 tag as decoration and wrote the Arabic post **in English** — silently, because the asset was correctly labelled `ar` either way. |
| `gen-3` | Restate the language *after* the source text; forbid copying from it. | `gen-2` still drifted to English and began reproducing the abstract near-verbatim. Both are recency effects: a long English prefix followed by a long English article is the worst possible context for an instruction sitting at the top. |
| `gen-4` | Drop the `hook` field; compute it instead. | The golden set showed the model returning **hashtags** in that field on every single case. The hook is definitionally the first sentence of the body, so asking a model to copy it was inviting an error that `first_sentence()` cannot make. The first change the eval suite paid for. |
| `gen-5` | Restate the character limit *after* the source text, as a word count as well. | The limit was stated once, in the cached prefix, thousands of tokens before the model started writing — the identical recency failure `gen-3` fixed for language, left in place for length because only language had been caught at the time. Words as well as characters because a model has no character counter: it cannot verify "under 280 characters" mid-sentence, and it can approximately track "about 42 words". |

Because the version is on the revision, the fix is *visible*: the Arabic asset
carries revisions at `gen-1` (0% Arabic script), `gen-2` (0%), and `gen-3`
(77% — the remainder being product names and figures, which the prompt asks to
keep in their original spelling).

## Where the prompt is split

Three pieces, and the boundary between them is a cost decision:

| Piece | Contents | Cached? |
|---|---|---|
| `cacheable_prefix` | Brand voice, few-shot exemplars, channel rules | **Yes** — identical for every story on that channel and language |
| `system` | The instruction: write only from the source | No, but tiny |
| `prompt` | Headline, source text, provenance, closing language **and length** reminder | No — different every story |

Cache reads bill at roughly a tenth of fresh input, so anything story-specific
placed ahead of the boundary quietly costs real money. A test asserts the
prefix is byte-identical across stories and contains no article text.

## Refusals

Counted by stable code, never papered over.

| Code | Meaning |
|---|---|
| `no_primary_source` | Nothing was read first-hand. |
| `over_channel_limit` | The model wrote past the channel's hard character limit. |
| `model_failed` | The provider call failed — see `docs/LLM.md`. |

**Over-length output is refused, not truncated.** A post cut off mid-word reads
as broken, and the fix is a prompt or a model, not a substring. In practice X's
280-character limit is where a small local model fails most often; LinkedIn's
3000 is comfortable.

## Token budget and Arabic

`ENGINE_GENERATE_MAX_TOKENS` defaults to 4096, sized for Arabic rather than
English. The same post costs roughly twice as many tokens in Arabic, and at
2048 a full-length Arabic LinkedIn post was observed running out of budget
mid-sentence while its English twin finished comfortably. The failure surfaced
as a `max_tokens` refusal rather than a truncated post, which is the intended
behaviour.

## Cost

```bash
engine cost report            # total, per asset, and recent runs
engine cost report --days 7
```

**The unit of spend is the revision, not the asset.** Token usage, model and
cost are recorded on each `ContentRevision`, because regenerating spends again:
an asset that took four attempts cost four times, and a cost-per-post figure
that hid the retries is exactly the figure that makes a pricing decision wrong.
`engine cost report` shows the attempt count alongside the total for that
reason.

Cost is priced **at the moment of generation** and stored, not recomputed on
read. A later price change must not rewrite what last month actually cost.

### The ceiling is checked before each call

`ENGINE_MAX_COST_USD_PER_RUN` (default `$5`) is enforced twice:

- The pipeline runner checks it **between stages**, as it does for every stage.
- The generate stage checks it **before each model call**. This is the one that
  matters here: three stories across three channels and two languages is
  eighteen calls inside a single stage, and a between-stages check would only
  notice the overspend once it had already happened.

Hitting the ceiling **halts the run rather than failing it**. Every asset
generated so far is already committed and was paid for; discarding it would
waste exactly the money the ceiling exists to protect. The run reports what it
stopped at:

```
Budget: stopped at $2.0000, at or over the $2.00 ceiling
```

On the default local provider every figure is `$0.000000`, the ceiling can
never fire, and the report says so explicitly — a table of zeroes otherwise
reads as broken instrumentation rather than as the accurate cost of running a
model on hardware you already own. See `docs/LLM.md`.

Revisions written before cost tracking existed report zero tokens and zero
cost: accurate for cost (they were generated locally, free) and simply
unrecorded for tokens.

## What is deliberately not here

- **No auto-publishing.** Assets are created in `draft`. The human review gate
  is a feature -- see [`REVIEW.md`](REVIEW.md) for what happens after it.
- **No per-claim evidence binding.** The asset records which evidence backed it
  (`evidence_ids`); binding individual generated sentences to sources is a
  later milestone.
- **No quality scoring.** Whether the output is good is currently a human
  judgement; golden-set evals come later in the roadmap.
