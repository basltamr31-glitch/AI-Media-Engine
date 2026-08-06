# 9. Transport fetchers with declaratively configured sources

Date: 2026-08-05

## Status

Accepted

## Context

Phase 1 ingests from three sources: a Gmail newsletter, a JSON API and an RSS
feed. The roadmap adds Reddit, Hacker News variants and GitHub Releases, and a
future customer will arrive wanting a source nobody has thought of yet.

The obvious implementation is one class per source: `TldrFetcher`,
`HackerNewsFetcher`, `RedditFetcher`. It is also the implementation that ends
with forty near-identical classes, a `if source.name == "..."` ladder somewhere
in the pipeline, and a codebase where "add a source" is a pull request, a review
and a deploy.

That matters commercially, not just aesthetically. If onboarding a customer's
preferred source requires an engineer, the marginal cost of a customer includes
engineering time, and the business does not scale.

## Decision

Fetchers are written **per transport, not per provider**, and each source is
described **declaratively in configuration**.

Three fetchers exist and we expect that number to stay roughly flat:

| Fetcher | Transport | Serves |
|---|---|---|
| `GmailFetcher` | `SourceKind.EMAIL` | any newsletter arriving by email |
| `RssFetcher` | `SourceKind.RSS` | any RSS or Atom feed |
| `JsonApiFetcher` | `SourceKind.API` | any JSON HTTP endpoint |

`JsonApiFetcher` carries the weight. It knows nothing about any provider; a
source tells it where the items live and how to read them:

```yaml
config:
  items_path: data.children        # dot-path to the array
  request:
    params: {t: day, limit: 50}
  field_map:                       # dot-paths within each item
    external_id: data.id
    title: data.title
    url: data.url
    published_at: data.created_utc
  filters:
    min: {data.score: 50}
```

Hacker News (`hits`), Reddit (`data.children`, doubly nested) and GitHub
Releases (a bare top-level array) are three genuinely different response shapes,
all handled by that one class. Tests assert this directly, so the property
cannot silently regress.

Two supporting decisions make it work:

* **Fetchers depend on ports, not SDKs.** `HttpClient` and `GmailApiClient` are
  narrow protocols. The `httpx` and `googleapiclient` imports are confined to
  one adapter each. Every fetcher is therefore unit-testable with no network, no
  Google account and no credentials.
* **The full payload is retained** as `raw_content`. A field map describes what
  we read *today*; Phase 2 will want fields nobody mapped, and for APIs with
  retention windows a re-fetch is impossible.

## Consequences

**Good**

- Adding Reddit, GitHub Releases, Product Hunt or Dev.to is a YAML entry. No
  code, no review, no deploy. A customer-specific source becomes a support task.
- Losing any one source degrades quality instead of stopping production, because
  no code path is specific to a provider.
- The whole test suite runs offline in under ten seconds.

**Costs, accepted**

- A declarative mapping is less expressive than code. A source needing pagination
  across cursors, or authentication beyond a header, will need either an
  extension to the config vocabulary or its own fetcher. That is the right place
  to spend complexity, and it is deferred until a real source demands it.
- Dot-path configuration errors surface at runtime rather than at import. The
  fetcher raises with the offending path and the actual type received, and a
  failing source is isolated rather than fatal — which is how the Hacker News
  misconfiguration was found and fixed within one run.

## Alternatives considered

**A class per provider.** Simplest to read for source #1, untenable by source
#10, and it puts provider names into the codebase where they become coupling.

**A generic scraper with LLM-based extraction.** Tempting and far too expensive:
a token cost on every item, non-deterministic parsing, and no way to test
offline. Deterministic mapping first; the LLM is reserved for work only an LLM
can do.
