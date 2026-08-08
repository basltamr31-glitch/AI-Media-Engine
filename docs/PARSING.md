# Parsing

Ingestion stores what arrived. Parsing turns each stored document into
**story candidates**: one row per story a document mentions, carrying the
headline, a summary, and — the part that matters most — the outbound link.

```bash
engine parse run                 # extract from documents that have none
engine parse run                 # again: extracts nothing new (idempotent)
engine parse status              # per-source counts and recent headlines
```

A candidate is not a story. The same launch appears in five newsletters with
five headlines; collapsing them is resolution, a separate step. And a
candidate's summary is **not publishable text** — it exists for traceability.
What gets published comes from the primary source (ADR 0003).

## What runs on what

A source's `parser_key` selects its parser. Sources without one are skipped,
and a `parser_key` naming a parser that is not installed is reported per source
rather than raising:

```yaml
- kind: email
  name: TLDR AI
  identifier: dan@tldrnewsletter.com
  parser_key: tldr_newsletter
  config:
    strip_sections: [sponsor, advertisement, together with]
```

| Parser | Handles | Status |
|---|---|---|
| `tldr_newsletter` | All eight TLDR editions | Shipped |
| `arxiv_rss` | arXiv feed entries | Shipped |
| `hackernews_api` | Hacker News items | Shipped |

## Idempotency and re-parsing

A document that already has candidates is skipped, so re-running costs nothing
and a crashed run is resumed by running it again.

Candidates are **derived** data: recomputable from a document that is itself
never deleted. That is what makes `--reparse` safe — it deletes and re-extracts
a document's candidates so a fixed parser can redo its work:

```bash
engine parse run --reparse                  # everything in the window
engine parse run --reparse --source "TLDR AI"
```

Off by default, because silently rewriting derived data on every run makes
"what changed?" unanswerable.

Every candidate records the parser and its `parser_version`, so a re-parsed
backlog can be told apart from an old one.

## The TLDR parser

TLDR's layout is consistent across editions: an `<h1>` section heading, then
blocks each holding one link and one summary. Every story link's text ends with
a parenthesised marker.

**The marker decides what is a story.** `(5 minute read)`, `(20 minute
podcast)`, `(GitHub Repo)`, `(Website)` are stories. `(Sponsor)` is a paid
placement and is dropped. `(Webinar)`, `(FREE)` and `(TLDR Curator, ~3
hrs/week)` are promotions and job adverts, and match nothing. Referral, jobs
and unsubscribe links carry no marker at all, so the footer needs no special
handling — which means a template change that moves it cannot start producing
job adverts as news.

`strip_sections` drops whole sections by heading, for editions that place ads
under a named section rather than marking each one.

**Tracking links are unwrapped, not stored.** Every TLDR link arrives as
`tracking.tldrnewsletter.com/CL0/<encoded destination>/1/<per-send token>`.
The token identifies the send, and therefore the subscriber. Only the encoded
destination is kept. A tracking URL that cannot be decoded yields no URL at all
and the candidate is flagged `unresolved_link` — better no link than one that
leaks who asked for it. Campaign parameters (`utm_*`) are stripped too, so the
same article linked from two newsletters produces one URL and corroboration can
actually detect it.

## The arXiv parser

One entry, one paper -- the opposite shape from a newsletter, which is a useful
check that nothing downstream assumes documents contain many stories.

The feed prepends `arXiv:2608.02604v1 Announce Type: new` to every abstract.
The identifier and version are split into `meta`, and the header is removed
from the summary.

`Announce Type` is the quality gate. `new` and `cross` are first appearances.
`replace` is a revision of a paper announced earlier -- not news, and covering
it would mean publishing the same paper twice. Set `announce_types` on the
source to change that.

## The Hacker News parser

Items tagged `job` are paid recruitment placements -- HN's sponsor block -- and
are dropped. `drop_tags` overrides the list.

Points and comment counts are carried into `meta` rather than discarded. The
API returns *current* counts, so the number observed today cannot be recovered
next week, and it is the raw material for a prominence signal in Phase 3.

A text post with no outbound link (an `Ask HN`) points at its own discussion
and is flagged `self_post`: for those, the thread *is* the primary source.

## Writing a parser

Implement `DocumentParser` and register it through an entry point, exactly as
the TLDR parser does (see [`PLUGINS.md`](PLUGINS.md)):

```python
class ArxivRssParser:
    @property
    def parser_key(self) -> str:
        return "arxiv_rss"

    def can_parse(self, document: SourceDocument) -> bool:
        """Cheap check before committing to a full parse."""

    def parse(self, document, *, config=None) -> list[StoryCandidate]:
        """config is the owning source's `config` block."""
```

```toml
[project.entry-points."engine.parsers"]
arxiv_rss = "my_package.arxiv:build_parser"
```

`parse` receives the source's config so that two sources sharing a format can
differ — the same property that makes adding a *feed* a config row makes adding
a *newsletter of a known format* one too.

Two rules worth keeping:

- **Extract leads, not prose.** Headline, gist, outbound link. Newsletter
  summaries are someone else's copyrighted text.
- **Fail loudly on your own document, quietly for everyone else's.** Raising is
  fine; the stage isolates it to that document and records the reason.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `no parser registered for '...'` | No installed package declares that `parser_key`. Check `engine plugins list --group parsers`. |
| A source shows 0 candidates and 0 failures | Its `parser_key` is unset, so it was never considered. |
| Candidates unchanged after fixing a parser | Extraction is idempotent. Use `engine parse run --reparse`. |
| A story has no `primary_url` | Its link could not be decoded to a destination; see `meta.unresolved_link`. |
| Counts drop after a template change | Expected and visible: `engine parse status`, then compare `parsed` against `failed` in `engine pipeline runs`. |
