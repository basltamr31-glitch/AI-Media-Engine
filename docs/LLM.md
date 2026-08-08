# Language models

The engine never imports a provider SDK. Everything that generates text depends
on `LLMPort` (`engine.core.ports.llm`), and the composition root decides which
implementation satisfies it. Moving from a local model to a hosted one is an
environment variable.

```
stage / command
      |  LLMRequest + Pydantic schema
      v
   LLMPort  ..................... engine.core.ports.llm
      |
      +-- OllamaClient ........... local, free, default
      +-- AnthropicClient ........ hosted, paid, opt-in
```

## Why local is the default

Generation quality has to be judged before it is paid for. A provider that
bills per token turns every prompt iteration into a purchase decision, which is
precisely the wrong incentive while the prompts are still bad. Running locally
makes the loop between "change the brand voice" and "read the result" free, so
it can be run a hundred times in an afternoon.

Two further consequences, both of which happen to matter here:

- **Scraped article text stays on this machine.** Enrichment reads third-party
  pages; sending them to a hosted model would be a second, undiscussed
  disclosure of somebody else's content.
- **There is no rate limit to design around**, so a full-corpus run is a matter
  of patience rather than quota.

The tradeoff is speed. On a laptop CPU a 4B model takes roughly 30–60 seconds
per generation. That is fine for development and would not be fine in
production, which is what the port exists to make a non-event.

## Configuration

| Setting | Default | Meaning |
| --- | --- | --- |
| `ENGINE_LLM_PROVIDER` | `ollama` | `ollama` or `anthropic`. |
| `ENGINE_OLLAMA_BASE_URL` | `http://localhost:11434` | Where the local server listens. |
| `ENGINE_OLLAMA_MODEL` | `qwen3:1.7b` | Any model `ollama pull` has fetched. |
| `ENGINE_OLLAMA_CONTEXT_TOKENS` | `8192` | Prompt window. See the truncation note below. |
| `ENGINE_OLLAMA_TIMEOUT_SECONDS` | `600` | Local generation is slow, not stuck. |
| `ENGINE_ANTHROPIC_API_KEY` | unset | Required only when the provider is `anthropic`. |
| `ENGINE_GENERATION_MODEL` | `claude-opus-5` | Used when the provider is `anthropic`. |

`Settings.active_model` resolves which of the two model settings applies, so no
caller can ask Ollama for `claude-opus-5`.

## Choosing a model

Any model `ollama pull` has fetched works — the adapter is model-agnostic, and
schema conformance comes from constrained decoding rather than from the model
being clever. What changes with size is the quality of the prose and how much
memory the machine needs to spare.

| Model | Download | RAM to run | Use it for |
| --- | --- | --- | --- |
| `qwen3:0.6b` | ~0.5 GB | ~1 GB | Proving the plumbing. Prose is weak. |
| `qwen3:1.7b` | ~1.4 GB | ~2 GB | **Default.** Extracts accurately from real articles. |
| `qwen3:4b` | ~2.6 GB | ~3.5 GB | Better prose; worth it once RAM allows. |

"RAM to run" is what has to be *free*, not installed. Check with
`engine doctor`; on a 16 GB laptop an IDE and a browser can easily leave under
3 GB, in which case a bigger model still runs but pages to disk and crawls.

Switching is one variable, no code change:

```bash
ENGINE_OLLAMA_MODEL=qwen3:4b engine llm check
```

## Setup

```bash
ollama pull qwen3:1.7b
```

Then prove it works:

```bash
engine llm check
```

That makes one real call and validates the response against a schema. Against
real corpus text at a realistic prompt size:

```bash
engine llm check --corpus
```

A model that handles a two-sentence prompt can still stall or drift on eight
thousand tokens of article, so the second command is the one that means
something.

## Schema conformance is the provider's job

`LLMPort.generate_structured` promises the returned value validates. Both
adapters get that from the provider rather than retrofitting it:

- **Ollama** takes a JSON Schema in its `format` parameter and constrains
  decoding against it — tokens that would break the schema are never sampled.
- **Anthropic** takes the Pydantic model as `output_format` on `messages.parse`.

Neither adapter strips code fences, hunts for the first `{`, or retries on
unparseable text. That kind of salvage would hide a misconfigured server behind
output that looks fine, and the port's docstring rules it out for that reason.
If bytes come back that do not validate, the call fails with
`code="schema_violation"`.

## Failure codes

Counted and branched on by code, never by slicing the message.

| Code | Meaning | Usual fix |
| --- | --- | --- |
| `provider_unreachable` | Nothing listening. | Start Ollama. |
| `model_not_found` | Model not installed. | `ollama pull <model>` |
| `timeout` | Generation outran the clock. | Raise the timeout, or use a smaller model. |
| `max_tokens` | Truncated before finishing. | Raise `max_tokens`. |
| `schema_violation` | Output did not validate. | Look at the prompt or the model. |
| `refusal` | Provider's classifiers declined. | Anthropic only. |
| `unknown_model_price` | No price on record. | Add it to `ANTHROPIC_PRICES`. |
| `missing_credentials` | Provider is `anthropic`, key is unset. | Set the key. |

## Cost

`OllamaClient.estimate_cost_usd` returns `0.0` — not a placeholder, but the
true number. Hardware you already own has no per-token price, and the ledger
stays meaningful because zero is honest.

`AnthropicClient` prices every call from the table in
`engine.adapters.llm.pricing`, which feeds the run ledger and therefore the
`ENGINE_MAX_COST_USD_PER_RUN` ceiling. Two rules that table follows:

- **An unknown model raises** rather than returning zero. Silently pricing a
  typo at nothing would defeat the one guard that exists to stop a runaway loop.
- **List price, never promotional price.** Introductory rates expire; a ledger
  that assumes a discount under-reports the moment it lapses. Over-estimating
  is the safe direction for a ceiling.

## Prompt caching

`LLMRequest.cacheable_prefix` carries the large, stable part of a prompt — a
brand profile, editorial rules — separately from the per-request content.

Against Anthropic it becomes a system block with a cache breakpoint, and cache
reads bill at roughly a tenth of fresh input. Ollama exposes no cache API, but
reuses its KV cache across calls sharing a prefix, so the prefix is still
placed first. It reports no cache counters, so `cache_read_tokens` stays `0`
rather than being invented.

Caching is a prefix match either way: anything that varies per request placed
ahead of the breakpoint invalidates everything behind it.

## The context-window trap

Ollama's default context window is small enough to silently truncate an
enriched article. A model writing confidently about the back half of a document
it never saw is worse than a failed run, so the adapter always sets `num_ctx`
explicitly and logs `llm.prompt_may_exceed_context` when the prompt looks too
big for it. Raise `ENGINE_OLLAMA_CONTEXT_TOKENS` if you see that warning —
noting that a larger window costs memory.

## Switching to Anthropic later

```bash
ENGINE_LLM_PROVIDER=anthropic
ENGINE_ANTHROPIC_API_KEY=sk-ant-...
pip install -e ".[llm]"
```

No stage, prompt or pipeline changes. The adapter is written and unit-tested
against a stubbed SDK, but **no paid call has ever been made through it** — the
request shape, usage mapping and cache placement are verified, the provider's
actual behaviour is not.
