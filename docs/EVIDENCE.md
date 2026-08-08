# Evidence

The provenance ledger: which sources support which claim.

```bash
engine evidence run              # reconcile provenance for recent stories
engine evidence show <story-id>  # what supports this story
```

Three things depend on this being stored data rather than a join someone
remembers to write:

- **A legal defence.** "Where did this sentence come from?" answered months
  later, from storage, without re-fetching anything.
- **A trust feature.** The sources behind a story are shown, not implied.
- **A debugging tool.** A story supported only by aggregators is one the engine
  should not be writing about — and that is now a query, not a judgement call.

## Kinds

| Kind | Means | How it is decided |
|---|---|---|
| `primary` | The paper, the announcement, the official blog | **Only ever declared**, via `config.evidence_kind` on the source |
| `secondary` | Reputable reporting on a primary source | A source linking to its own site, or a page enrichment read first-hand |
| `aggregator` | A newsletter or feed that surfaced it | A source linking somewhere else |

Nothing is called `primary` by inference. Whether a page is the actor's own
announcement or a journalist's report of it is not something a URL can tell
you, and over-claiming provenance is precisely the failure this ledger exists
to prevent. Where the answer *is* known, the source says so:

```yaml
- kind: rss
  name: arXiv cs.AI
  identifier: https://export.arxiv.org/rss/cs.AI
  config:
    evidence_kind: primary     # arXiv publishes the authors' own words
```

Having read a page yourself rules out treating it as mere aggregation, so
enrichment produces at least `secondary` — but reading a news report does not
turn it into an announcement, so it never promotes an aggregator to `primary`.

## Reconciliation

Evidence is derived, and reconciled rather than appended: each run records what
the story's current mentions and enrichment justify, and skips what is already
there. Running twice adds nothing. A story that gains a mention gains exactly
one record.

`UNIQUE(story_id, kind, source_url)` enforces that at the database level, so a
bug in the stage cannot quietly produce a duplicate ledger.

Discarding stories (`engine stories reset`) discards their evidence too. That
is correct: evidence supports a specific story and has no meaning without it,
and the next evidence run rebuilds it from the mentions, which are never
deleted.

## What Phase 2 records, and what Phase 3 adds

Today the *claim* is the story's own headline, supported by the sources that
carried it — provenance-level evidence.

Phase 3 adds claim-level evidence, binding each generated sentence to the
passage that supports it. Same table, same kinds, same reconciliation; only the
granularity changes. The `quote` field, hard-capped at 25 words by the domain
model, is there for exactly that and is unused until then.

## Reading the output

```
engine evidence run
```

```
 Stories  Recorded  Aggregator-only stories
     523       330                      437
Ledger: 531 records -- aggregator=445, primary=80, secondary=6
```

`Aggregator-only stories` is the number that matters editorially. It counts
stories nobody has read first-hand, and it is the gap `engine enrich run`
closes.
