# ADR 0019: robots.txt and rate limiting are transport policy, not stage logic

- **Status:** Accepted
- **Date:** Phase 6

## Context

Enrichment fetches the primary source behind each story. Through Phases 2–5 that
was operator-triggered, sequential and measured in dozens of pages per run —
behaviour indistinguishable from a person following a link they had been sent.
`docs/FUTURE_IDEAS.md` recorded the deferral explicitly, with a trigger:

> A robots check (with caching, since one host serves many stories) is needed
> before it runs unattended or at volume.
> *Revisit when:* enrichment moves to a schedule.

Phase 6 moves it to a schedule. The trigger has fired.

Two things had to be true before an unattended fetcher is acceptable, and
neither was:

1. **Permission.** `robots.txt` is how a site states what automated clients may
   fetch. Ignoring it while running unattended is the difference between a tool
   and a nuisance, and it is the kind of thing that gets a user agent blocked
   by an entire CDN rather than by one publisher.
2. **Pacing.** One host frequently serves many stories. A burst of requests is
   what triggers blocking, and the resulting failure is silent and
   misattributed: extraction starts failing, and it looks like an extractor bug.

The question is where these belong.

## Decision

Both are implemented as a **decorator over the `HttpClient` port**, composed in
the container, and no stage or fetcher knows they exist.

```
HttpxClient          transport, retries, backoff, timeouts
  wrapped by
PoliteHttpClient     robots check, per-host pacing
```

`RobotsPolicy` caches per origin with a TTL and shares the `RateLimiter` with
the client that wraps it, so fetching `robots.txt` is itself paced and counted —
otherwise a run over 25 hosts issues 50 requests while believing it issued 25.

A refusal raises `RobotsDisallowedError`, a **subclass** of `HttpError`. Callers
that treat all transport problems alike keep working unchanged; the enrich stage
catches it specifically and records `robots_disallowed` rather than
`fetch_failed`.

### Following RFC 9309 where it is counter-intuitive

The specification draws a distinction most implementations collapse:

| Response to `/robots.txt` | Meaning | Decision |
|---|---|---|
| 404 / other 4xx | The site publishes no rules | **Allow everything** |
| 5xx, timeout, connection error | Rules exist; we failed to read them | **Disallow everything** |

These are opposite answers to superficially similar failures. Guessing "allowed"
on an unreachable file is how a crawler ignores a disallow during exactly the
outage that made the server fragile.

`Crawl-delay` is read from the **same user-agent group that governs
permission**, so a site addressing us by name sets both, and a named group
without a delay does not silently inherit the wildcard's. It is capped at 30
seconds: past that point the correct response is to fetch fewer pages, not to
stall the run.

## Consequences

**Good**

- A fetcher written next year is polite without its author doing anything.
- Politeness is testable without a network: the whole policy is unit-tested
  against a fake client and an injected clock.
- Turning it off is a setting (`ENGINE_HTTP_RESPECT_ROBOTS`), for the legitimate
  case of fetching one's own sites, and it is not a flag on the command that
  would make it convenient to do to somebody else's.
- The refusal is a distinct failure category, so "12 refused by robots" and
  "12 requests failed" are never confused. Only one of them is a bug.

**Costs, accepted**

- One extra request per host per day. Bounded by the cache, and small against
  the pages that request authorises.
- Runs are slower by the pacing interval times the number of same-host fetches.
  This is the intended cost, not a regression.
- Some primary sources become permanently unreachable. That is the correct
  outcome; the alternative is content built on a fetch we were asked not to
  make.

## Alternatives considered

**Check `robots.txt` inside the enrich stage.** Fewer moving parts today. It
puts transport policy in a pipeline stage, so the next component that fetches
anything — a PDF extractor, an oEmbed lookup, a Phase 7 API — is impolite by
default, and the omission is invisible until somebody complains.

**A third-party robots library.** `urllib.robotparser` is in the standard
library, implements the group-matching and `Crawl-delay` semantics needed here,
and adds no dependency to a project whose dependency list is a deliberate
asset. The parts it does not do — caching, the 4xx/5xx distinction, the request
pacing — are the parts that had to be written anyway.

**Skip robots and rely on the low request volume.** This is what Phases 2–5
did, and the justification was volume plus a human in the loop. Scheduling
removes both halves of it at once.
