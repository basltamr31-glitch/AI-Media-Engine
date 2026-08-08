# Scoring

Editorial judgement is the product. This is where it becomes a number — and
every number shows its working.

```bash
engine score run                 # rank recent stories
engine score top                 # the current leaderboard
engine score show <story-id>     # why this story ranked where it did
```

## How a score is built

Each ranking signal the active pack declares has a **computer** that measures it
in its own units, and a **normaliser** that maps those units onto 0..1. The
workspace's scoring profile weights them, and the weighted parts are stored
individually:

```
| Signal           | Measured | Normaliser  | Weight | Contribution |
| corroboration    |     1.00 | linear      |   0.30 |       0.1000 |
| source_authority |     0.60 | passthrough |   0.20 |       0.1200 |
| recency          |     0.16 | decay       |   0.20 |       0.1991 |
| prominence       |    10.00 | linear      |   0.15 |       0.1500 |
| audience_fit     |     0.00 | passthrough |   0.15 |       0.0000 |
```

Nothing enters the total that is not also shown. A ranking nobody can
interrogate is a ranking nobody will trust — least of all when it puts the
wrong story first.

Scoring is pure arithmetic over rows already loaded: no network call, no model,
no per-story cost. Ranking runs over every story on every run, so that is
load-bearing rather than incidental.

## The shipped signals

| Signal | Measures | Notes |
|---|---|---|
| `corroboration` | Distinct sources reporting it | Counts *sources*, not mentions — one newsletter repeating itself all week still counts once |
| `source_authority` | Trust of the best source carrying it | The maximum, not the average: an aggregator also mentioning a story does not make it less reliable |
| `recency` | Age in hours, decayed | Measured from *first* seen, so a re-mentioned story is not new again |
| `prominence` | How high the source placed it | Newsletter position or community votes — two expressions of one judgement, so they do not stack |
| `audience_fit` | Overlap with the workspace's priority topics | Blocked topics score zero outright |

## Unmeasurable is not zero

A signal that cannot be measured for a story is **excluded** from both the
total and the weight it would have carried, and the total is rebased over what
remained. Scoring it as zero would punish a story for the engine's ignorance.

This matters more than it sounds. An arXiv entry is position 0 in its feed item
because that item contains one paper — not because anyone judged it the day's
most important story. Treating that as a lead item handed every paper in the
feed full prominence marks, and the whole source ranked identically as a
result. `prominence` now returns "not measurable" where the source never made a
choice, and `score show` lists what was excluded.

## Weights

From the workspace's `editorial.scoring.weights` if set, from the pack's
declared defaults otherwise. Both must sum to 1.0 (ADR 0016).

```yaml
editorial:
  scoring:
    weights:
      corroboration: 0.30
      source_authority: 0.20
      recency: 0.20
      prominence: 0.15
      audience_fit: 0.15
```

## Scores are history

Re-running does not re-score; `--rescore` adds a *new* score rather than
replacing the old one. "What did we think last Tuesday, and with which
weights?" is the question that makes refitting the weights against measured
engagement possible in Phase 5, and an upsert would erase it.

Each score keeps the **raw** measurements alongside the weighted components,
because the raw value cannot be recovered from the weighted one — and without
it there are no training inputs.

## Adding a signal for another vertical

A computer is a plugin like any other (see [`PLUGINS.md`](PLUGINS.md)):

```python
class CvssSeverityComputer:
    @property
    def key(self) -> str:
        return "cvss_severity"

    def compute(self, story, context) -> float | None:
        """In your own units. Return None when it cannot be measured."""
```

```toml
[project.entry-points."engine.signals"]
cvss_severity = "engine_secpack.signals:build_cvss_severity"
```

Declare the signal in your pack with a normaliser and a default weight, and it
enters the ranking with no engine change.

## Selection

Scoring ranks everything. Selection says no.

```bash
engine select run                # choose what is worth producing
engine select list               # what editorial chose
```

Three gates, all from the workspace's `editorial` policy:

| Gate | Refusal | Why |
|---|---|---|
| `minimum_score` | `below_minimum_score` | A thin news day should produce a thin day's output, not the best of a bad set dressed up as news |
| `require_primary_source` | `no_primary_source` | A story nobody read first-hand is second-hand; producing from it makes the engine a republisher |
| `max_stories_per_run` | `quota_reached` | Selectivity is the product; volume is not |

A run that selects nothing says which gate did it, because "a thin news day"
and "the pipeline is broken" look identical otherwise:

```
Quota 3 per 24h  (minimum score 0.35, primary source required)
                  Not selected
 Reason                              Stories
 no primary source read first-hand      435
 good enough, but the quota was full      85
```

**The quota is counted over a window, not per invocation.** Running the command
twice in one morning tops up to the day's limit rather than quietly producing a
second batch; the window frees the next day. `--limit` overrides the policy for
one run.

**Nothing is ever marked rejected.** A story that missed today's cut may be
exactly right tomorrow — corroboration arrives late, enrichment succeeds on a
retry — and a status saying "rejected" would make that unreachable. Not
selected is simply not selected.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Every story scores the same | Usually one signal giving everyone full marks. `score show` a couple and compare contributions. |
| `No computer for: X` | A pack declares a signal nothing measures. The signal is excluded; install or write its computer. |
| Everything is above `minimum_score` | Expected on a fresh corpus, where recency is ~1.0 for everything. It discriminates as stories age. |
| Scores did not change after editing weights | Scoring is idempotent. Use `engine score run --rescore`. |
| `select run` chooses nothing | Read the refusal table. `no primary source` needs `enrich run`; `not scored` needs `score run`. |
| `select run` chooses nothing on a second call | The quota is already spent for the window. Expected. |
