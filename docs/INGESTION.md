# Ingestion

How documents get into the engine, and how to add a source without writing code.

---

## The model

```
config/workspaces/<slug>.yaml     declares WHAT to fetch
        │
        │  engine workspace seed
        ▼
   sources table                  the reconciled tenant configuration
        │
        │  engine ingest run
        ▼
  FetcherRegistry                 dispatches by TRANSPORT, not by provider
        │
        ├── GmailFetcher      SourceKind.EMAIL
        ├── RssFetcher        SourceKind.RSS
        └── JsonApiFetcher    SourceKind.API
        │
        ▼
   SourceDocument                 deduplicated by content hash
        │
        ├── source_documents table
        └── data/raw/<slug>/<date>/<id>.json     verbatim archive
```

Three fetchers, one per transport. Adding a *source* does not add a fetcher.

---

## Source kinds

| Kind | Fetcher | Use for |
|---|---|---|
| `email` | `GmailFetcher` | Any newsletter arriving in Gmail — TLDR AI, Ben's Bites, Import AI |
| `rss` | `RssFetcher` | Any RSS or Atom feed — arXiv, company blogs, GitHub `releases.atom` |
| `api` | `JsonApiFetcher` | Any JSON HTTP endpoint — Hacker News, Reddit, GitHub, Product Hunt |
| `web` | *(reserved)* | Direct page scraping, deferred until a source genuinely requires it |

---

## Adding a source: three worked examples

Every one of these is **configuration only**. Paste into the `sources:` list in
`config/workspaces/default.yaml`, run `engine workspace seed`, done.

### Reddit

Reddit nests items twice: the array is at `data.children`, and each item's
fields sit under another `data`. Dot-paths handle both.

```yaml
  - kind: api
    name: r/MachineLearning
    identifier: https://www.reddit.com/r/MachineLearning/top.json
    parser_key: reddit_api
    trust_weight: 0.40
    config:
      items_path: data.children
      request:
        params: {t: day, limit: 50}
      field_map:
        external_id: data.id
        title: data.title
        url: data.url
        author: data.author
        published_at: data.created_utc    # epoch seconds, parsed transparently
        summary: data.selftext
      filters:
        min: {data.score: 50}
```

### Hacker News

Items live under `hits`. Already configured in `default.yaml`.

```yaml
  - kind: api
    name: Hacker News (front page)
    identifier: https://hn.algolia.com/api/v1/search
    trust_weight: 0.45
    config:
      items_path: hits
      request:
        params: {tags: front_page, hitsPerPage: 50}
      field_map:
        external_id: objectID
        title: title
        url: url
        author: author
        published_at: created_at
      filters:
        min: {points: 100}
        require: [title]
```

### GitHub Releases

The payload *is* the array, so `items_path` is empty. Drafts and release
candidates are excluded.

```yaml
  - kind: api
    name: vLLM releases
    identifier: https://api.github.com/repos/vllm-project/vllm/releases
    trust_weight: 0.80
    config:
      items_path: ""
      field_map:
        external_id: id
        title: name
        url: html_url
        author: author.login
        published_at: published_at
        summary: body
      filters:
        exclude_true: [draft, prerelease]
```

---

## JSON API configuration reference

| Key | Meaning |
|---|---|
| `items_path` | Dot-path to the array of items. `""` means the payload is the array. |
| `request.params` | Query-string parameters. |
| `request.headers` | Extra request headers. |
| `field_map.*` | Dot-path *within each item* for `external_id`, `title`, `url`, `author`, `published_at`, `summary`. |
| `filters.min` / `filters.max` | Numeric thresholds, e.g. `{points: 100}`. A missing or non-numeric field **fails** the check — filters are a floor. |
| `filters.require` | Field paths that must be present and non-empty. |
| `filters.exclude_true` | Field paths that must not be truthy. |
| `max_items` | Per-source cap, further limited by `ENGINE_INGEST_MAX_DOCUMENTS`. |

Dates are parsed from ISO 8601, RFC 2822, or Unix epoch (seconds or
milliseconds) and always stored as UTC. An unparseable date costs the timestamp,
never the item.

---

## Gmail-specific configuration

```yaml
  - kind: email
    name: TLDR AI
    identifier: dan@tldrnewsletter.com     # the sender address
    parser_key: tldr_newsletter
    config:
      lookback_days: 7                     # overrides ENGINE_INGEST_LOOKBACK_DAYS
      label: Newsletters                   # optional Gmail label filter
      subject: "TLDR AI"                   # optional, when one sender sends several
      query: "-category:promotions"        # optional raw Gmail search terms
```

The generated Gmail query is visible in the logs on every run:

```
from:(dan@tldrnewsletter.com) newer_than:7d
```

Gmail access is **read-only** (`gmail.readonly`). The engine never marks mail as
read, never labels it and never deletes it — processed state lives in our own
database. Your inbox is untouched.

---

## Idempotency

Every document gets a SHA-256 `content_hash`. Before insert, the stage checks
whether that hash already exists for the workspace, both against the database and
against hashes seen earlier in the same run.

The practical guarantees:

- Running `engine ingest run` twice in a row stores nothing the second time.
- A crashed run is resumed by running it again.
- Two sources surfacing the same story store it once.
- Scheduling it hourly is safe.

`engine ingest run` reports `Fetched / New / Duplicate` per source so this is
observable rather than assumed.

---

## Fault isolation

One source failing does not stop the others. A failing source is recorded in the
stage ledger with its error and the run still succeeds:

```
| Source                    | Kind  | Fetched | New | Duplicate | Error            |
| Hacker News (front page)  | api   |      11 |  11 |         0 |                  |
| TLDR AI                   | email |       0 |   0 |         0 | GoogleAuthError… |
| arXiv cs.AI               | rss   |      40 |   0 |        40 |                  |
```

If **every** source fails, that is systemic — no network, expired credentials,
bad configuration — and the run fails loudly.

---

## The raw archive

Verbatim payloads are written to `data/raw/<workspace>/<YYYY-MM-DD>/<id>.json`,
each a self-describing envelope containing the metadata and the original content.

It exists for three reasons:

1. **Replay.** Phase 2 parsers can be re-run against real historical payloads
   without re-fetching — often impossible for email.
2. **Fixtures.** Parsers get built against real newsletters, not imagined ones.
3. **Provenance.** If a published claim is challenged, the original bytes are
   still on disk.

Disable with `ENGINE_INGEST_ARCHIVE_RAW=false`. The directory is gitignored.

---

## Source identity and reconciliation

`engine workspace seed` reconciles the database against the YAML file:

- Sources are matched **by name**, which is unique within a workspace.
- A changed `identifier` **updates the existing source**, keeping its history.
  (arXiv moving from `http` to `https` must not fork a second source.)
- A source removed from config is **deactivated, never deleted** — its ingested
  documents are evidence.
- Re-adding it reactivates it.

Two sources may share an endpoint under different names, which is how you run one
Hacker News query at two attention thresholds.

See [ADR 0010](adr/0010-source-name-as-natural-key.md) for why.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `GoogleAuthError: no cached Google token` | Run `engine sources auth`. |
| `could not refresh the Google token` | OAuth app still in *Testing* status expires refresh tokens after 7 days. Re-run `engine sources auth`, or set the app to *In production* in Google Cloud Console. |
| `items_path '...' did not resolve to a list (got dict)` | Wrong `items_path`. Fetch the URL in a browser and find the array. |
| A source fetches 0 items with no error | Filters are too strict, or the Gmail query matches nothing. The query is printed in the logs. |
| `no fetcher registered for kind ...` | The source `kind` is not one of `email`, `rss`, `api`. |
| Duplicate sources with the same name | Fixed by migration `b2c41f7d9e33`; run `engine db upgrade` then `engine workspace seed`. |
