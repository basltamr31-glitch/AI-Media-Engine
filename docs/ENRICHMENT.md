# Enrichment

Parsing gives a story a *lead*: a headline and a link. Enrichment follows that
link and reads what is actually there.

```bash
engine enrich run                # fetch the primary source for waiting stories
engine enrich status             # coverage, and what could not be read
```

This is what makes "newsletters are tips, not text" real. A story with no
enrichment is second-hand; `Story.is_publishable` is False, and editorial
selection in Phase 3 cannot choose it however well it scores.

## What gets enriched

Stories that are `resolved`, carry a `primary_url`, have no enrichment yet, and
have attempts left. Oldest first, so a story waiting since Monday is not
starved by this morning's arrivals.

Successful enrichment moves the story to `enriched` and stores the extracted
text, title, author and publication date, along with which extractor produced
them.

## Quality gates

Fetching a page is not the same as understanding it. Three things are checked
before anything is stored, because each failure they prevent is silent:

| Gate | Why |
|---|---|
| Content type must be HTML | A PDF run through an HTML extractor produces plausible nonsense |
| Extracted text ≥ 400 characters | Paywall stubs and consent walls extract *cleanly*, and a post written from two sentences reads as though we had read the article |
| Text must exist at all | A JavaScript shell parses fine and contains nothing |

Every rejection is recorded on the story with its full reason — URL, status,
character count — so `engine enrich status` answers "why is this not enriched?"
without re-fetching.

The run ledger records a short **code** instead, because it counts them:

| Code | Meaning |
|---|---|
| `fetch_failed_401` / `_403` / `_404` | The server refused or the page is gone. The status is part of the code: a paywall and a block need different responses. |
| `unsupported_content_type` | A PDF or image behind an HTML-looking URL |
| `text_too_short` | Extracted, but a stub — paywall or consent wall |
| `no_article_text` | Parsed fine, contained nothing — usually a JavaScript shell |
| `empty_document` | The response body was empty |
| `extractor_error` | The extractor raised something unforeseen |
| `no_primary_url` | The story has no link to follow |

Codes never embed anything that varies per page. A character count or a URL in
the label turns one category into hundreds and makes the counts meaningless.

## Failure budget

Most enrichment failures are permanent: a paywall, a dead link, or a server
that refuses non-browser clients will fail again tomorrow. Attempts are counted
on the story, and after three (`ENGINE_ENRICH_MAX_ATTEMPTS`) the URL is left
alone. Without that, every morning's budget would go on the same dead links.

A later success clears both the counter and the recorded error.

## Being a good citizen

One story per request, sequentially, with a per-run ceiling of 25
(`ENGINE_ENRICH_MAX_STORIES`) and the engine's identifying user-agent.
Concurrency would be easy and would make the engine a nuisance.

**`robots.txt` is obeyed**, and requests to one host are spaced by at least a
second or by the site's own `Crawl-delay`, whichever is longer. Neither is
implemented here: both live in a decorator over the HTTP port, so every fetcher
in the engine inherits them ([ADR 0019](adr/0019-politeness-as-transport-policy.md)).

A refusal is recorded as `robots_disallowed`, deliberately separate from the
fetch failures. Retrying will not help, nothing is broken, and an operator
reading "12 refused by robots" knows something entirely different from "12
requests failed".

This arrived in Phase 6 with scheduling, and the two are connected: through
Phases 2–5 enrichment was an operator-triggered fetch of pages a human had
already been sent a link to, which is materially different from crawling.
Running on a timer removed both halves of that justification at once.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `text_too_short` | Paywall, consent wall, or a page rendered by JavaScript. Nothing to do — the link is not readable. |
| `unsupported_content_type` | The primary source is a PDF. A PDF extractor would unlock these; `enrich status` lists exactly which. |
| `fetch_failed_403` | The server refuses non-browser clients. Common for link-shorteners. |
| `fetch_failed_401` | The destination is paywalled (WSJ and similar, reached through a newsletter's shortener). |
| A story is never attempted | It has no `primary_url`, or it has exhausted its attempts. |
| Everything fails at once | Check connectivity before assuming the web changed: partial failure is normal, total failure is systemic. |
