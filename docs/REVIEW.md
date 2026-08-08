# Review, publish, measure

The loop that turns generated drafts into published posts, and published posts
into data that ranking can learn from.

```bash
engine review queue                    # what is waiting for a decision
engine review show <asset-id>          # read one, --history for the chain
engine review edit <asset-id> -f post.txt
engine review approve <asset-id>       # clears it for publication
engine review reject <asset-id> --reason "reads like the abstract"

engine export run                      # approved posts -> files to paste
engine export show                     # what is in the folder, and what is wrong

engine publish record <asset-id> --url https://linkedin.com/posts/...
engine publish list

engine metrics add <publication-id> -m impressions -v 4210
engine metrics report
```

## The state machine is the product

```
draft ──▶ in_review ──▶ approved ──▶ published ──▶ archived
  ▲          │  │           │
  └──────────┘  └──▶ rejected
      edit
```

Illegal moves raise. That is not defensiveness — it is the one guarantee that
makes this usable by anyone who has to answer for what got posted: **nothing
reaches a channel without a human decision.** `approve` walks a draft through
`in_review` rather than skipping it, because a shortcut past a state is the same
hole with a friendlier name.

Two rules follow from it and are worth knowing before they surprise you:

- **Editing approved content returns it to draft.** An approval refers to the
  text that was approved. Leaving it approved after a rewrite would mean the
  gate had been passed by words nobody read.
- **Published content cannot be edited.** The post is live; editing the record
  of it would make the archive disagree with the world. Publish a correction as
  new content.

## Every action appends; nothing is overwritten

Rejecting does not delete the draft. Editing does not replace the generated
text. Correcting a metric does not update the old reading.

This is the same decision as [ADR 0013](adr/0013-content-revision-chain.md), and
the reason is commercial rather than tidy: the difference between the
`generated` revision and the `edited` one that actually got published is a
labelled preference pair in the customer's own voice, produced as a by-product
of work they were doing anyway. Overwriting `body` would discard it silently and
permanently.

So an edit *measures* the difference and stores the measurement:

| Field | What it says |
|---|---|
| `similarity` | 1.0 unchanged, 0.0 nothing in common |
| `characters_changed` | signed: negative when the human cut |
| `is_rewrite` | similarity below 0.5 — replaced, not adjusted |

The comparison is character-level, not word-level, and that choice is about
Arabic. Arabic marks agreement, definiteness and possession with affixes, so a
one-letter correction changes the *inside* of a word. A word-level ratio would
score `نموذجا` → `نموذجًا` as a whole-token replacement and report a rewrite
where an editor would say a diacritic was added — and the resulting number would
claim the generator is failing when it is not.

Every measure here is algorithmic. Nothing calls a model. A measurement that
costs a model call per edit is one that gets switched off in the month it starts
to matter.

## Who did it

Two actors are provisioned with every account:

| Key | Kind | Used for |
|---|---|---|
| `system` | system | everything the pipeline does unattended |
| `owner` | human | review decisions, edits, publication and metric entry |

Review commands default to `owner` and take `--as <key>` for anyone else. An
unknown key is **refused, not created**: conjuring an actor from a CLI flag
would let a typo become a second identity, and an audit trail whose actors are
created by misspelling is not one.

The distinction is not bookkeeping. If a human edit were attributed to the
system actor, the machine-versus-human difference — the entire training signal —
would be destroyed at the moment of capture, and no later query could recover it.

## The signal log

Every action emits a signal ([ADR 0012](adr/0012-signal-log.md)). The state
machine records where content *is*; the signal log records *what happened to it
and why*, which is the part a learner can consume.

| Signal | Polarity | Carries |
|---|---|---|
| `content.edited` | negative | the edit delta, prompt version, model |
| `content.approved` | positive | whether the approved text was generated or rewritten |
| `content.rejected` | negative | the reason, as a label |
| `content.published` | positive | publication id, channel, story |
| `publication.engagement` | neutral | the metric name and value |

`content.approved` carrying `revision_origin` is the one worth pointing at:
approval of *untouched* model output is the strongest evidence the generator is
working, and approval after a rewrite says the opposite. Same state, opposite
meaning.

`content.generated` is deliberately **not** emitted. The revision chain already
records every generation with attribution, model, prompt version and cost; a
second copy in the signal log would be a duplicate that can drift.

## Export: exactly what goes in the box

`engine export run` writes one file per approved post:

```
output/<workspace>/<YYYY-MM-DD>/export/
├── linkedin-en-ast_01K….txt
├── linkedin-ar-ast_01K….txt
└── manifest.json
```

Each `.txt` contains the body, the hashtags the channel allows, and the source
URL if the channel supports links — nothing else. No headings, no metadata, no
provenance. Select all, copy, paste.

The channel's rules come from the pack ([ADR 0015](adr/0015-open-vocabularies.md)),
so a channel that forbids links drops the link and one that allows two hashtags
gets two. The character limit is checked against the **assembled** text rather
than the body the model wrote, because the hashtags and the URL count too: a
post that fits before they are appended is not a post that fits.

Nothing over the limit is truncated. Truncating would end a sentence mid-word in
front of an audience; the file is written in full and the problem is named in
`manifest.json` and by `engine export show`.

Export is driven by **approval state, not a time window** — that is the
difference between it and [`render`](RENDERING.md). A post approved on Friday and
published on Monday is normal; filing by generation time would scatter one
publishing session across three folders.

Exporting changes no state. Writing a file is not evidence that anyone posted it.

## Publication is recorded, not performed

The engine does not post to any platform. No OAuth, no scheduler, no automated
posting — the reasoning is in
[ADR 0018](adr/0018-record-publication-do-not-perform-it.md).

`engine publish record` is a human stating a fact, which is why `--at` accepts a
past time. Posts get recorded after they went out, and stamping them with the
moment of data entry would corrupt every timing question the log exists to
answer. A time in the *future* is refused: nobody has published something that
has not happened.

One publication per asset per channel, enforced by a unique constraint.
Recording the same post twice would double every metric that joins through it,
and a silently doubled engagement number is worse than a missing one.

## Measurement closes the loop

```bash
engine metrics add pub_01K… -m impressions -v 4210
engine metrics add pub_01K… -m likes -v 87        # Tuesday
engine metrics add pub_01K… -m likes -v 103       # Friday
```

Both `likes` readings are kept. A post's count on Tuesday and its count on
Friday are two facts, not one fact revised — keeping both is what makes growth
over time recoverable, and overwriting would collapse the only series the
feedback loop can learn from.

`engine metrics report` collapses each metric to its **latest** reading, because
"how is this post doing?" is a question about now, and totals per channel because
"where does this work?" is a question about aggregates. Publications with no
readings are listed too: knowing what has *not* been measured is half the point
of the report.

Entry is by hand, and that is an ordering decision rather than a shortcut. A
number typed in after a week closes the loop; per-platform OAuth is weeks of work
that closes nothing until the last integration lands. `collected_by` is the only
field that changes when collection is automated, so the report, the signals and
anything learning from them keep working untouched.

## What this does not do yet

- **Nothing consumes the engagement data.** Phase 5 produces the dataset and the
  report; refitting ranking weights against it is Phase 6 work. The schema was
  built for it ([ADR 0016](adr/0016-open-scoring-profile.md)) — the weights are an
  open map and the per-signal inputs are stored — but no learner exists.
- **No scheduling.** There is no "publish at 09:00" queue, because there is no
  publishing.
- **No per-platform metric definitions.** `impressions` on LinkedIn and
  `impressions` on X are stored under the same name and are not the same thing.
  The metric vocabulary is open (`metric` is a string, normalised to
  lowercase-with-underscores), so a pack can declare its own when that matters.
