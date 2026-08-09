# Golden-set evals

Quality regressions are invisible by default. A prompt change that starts
writing the Arabic post in English produces an asset that is correctly
labelled, correctly stored, correctly costed — and wrong. Nothing in the
pipeline notices. That is what this exists to fix.

```bash
engine eval list
engine eval run                              # score against the baseline
engine eval run --repeat 3 --save-baseline   # record a new expectation
engine eval run --case matraix_ar_linkedin   # one case
```

Exit code is non-zero on a failure or a regression, so it works in CI.

## Every check is an algorithm, not a judge

An LLM-as-judge costs a model call per case per run. That is the shape of spend
that makes a suite too expensive to run often — and a suite nobody runs is
worse than none, because it looks like coverage. So every check here is a
regex, a set intersection or a ratio. **The whole suite costs nothing**, which
is what makes it reasonable to run after every prompt edit.

| Check | Catches | How |
|---|---|---|
| `language` | The Arabic profile writing English | Ratio of letters in the expected script |
| `not_copied` | Reproducing the source instead of writing about it | Shared 8-word runs with the source |
| `numbers_supported` | **Invented figures** | Every number in the post must appear in the source |
| `length` | Output the channel cannot accept | Character count |
| `hook` | An empty or unpunctuated post | The hook must open the body |
| `must_include` | Omitting the subject | Substring |
| `no_fused_words` | One token welded from two scripts | Arabic and Latin letters inside a single word |

`no_fused_words` was added last, and not by design — by reading a real Arabic
post. It contained one token that was the Arabic word for "art" welded onto the
tail of the English "financial", and **every other check passed it**: 78% Arabic
script, nothing copied, no unsupported number, length fine. Unreadable to an
Arabic speaker, invisible to everyone else — the same shape of failure as the
RTL bug.

It catches *fusion*, not fluency. The same post left "Deliverables"
untranslated four times and contained malformed words, and no algorithmic check
here detects that. Those need a human, which is what the review gate is for.

`numbers_supported` is the one that matters most. A model that invents a
plausible number produces output that reads perfectly and is false, which is
the failure a human reviewer is least likely to catch. It is deliberately
strict: a figure the post *derived* rather than copied will fail, because the
prompt tells the model not to compute.

Each check is a *score* in 0..1 against a threshold, not a boolean, so a
regression is visible as movement before it becomes a failure.

## The cases

`evals/golden/*.yaml`, one file per case. They are real arXiv abstracts from
the corpus, stored verbatim rather than re-fetched — an eval whose inputs
change under it is an eval whose failures get ignored.

The set deliberately covers both languages and one tight character limit (X's
280, where a small model fails most often). A set of only long-form English
cases would score well and prove nothing.

Adding a case is writing a YAML file. That is the point: the people who can
tell whether a post is good are not necessarily the people who write tests.

The eval uses a plain, fixed brand voice rather than the shipped profiles.
Otherwise every edit to a brand profile would register as a quality regression,
and the suite measures whether the *prompt scaffolding* holds up.

## Sampling: one run is not a measurement

Generation is non-deterministic, and on a small model the spread is large. Two
consecutive runs of this suite scored **0.553** and **0.907** with no code
change in between. A tolerance tight enough to catch a real regression would
fire constantly on that noise.

So `--repeat N` samples each case N times and keeps the **median** — median
rather than mean, because one failed call would drag an average down far enough
to look like a collapse. Record baselines with `--repeat 3` or more; a bare
`--save-baseline` warns you it is recording a coin toss.

The baseline stores the sample count, so a careful baseline is never silently
compared against a noisy run.

## The baseline

`evals/baseline.json`, checked in. It records the model and prompt version
alongside the scores, because a score without them cannot be compared to
anything:

```json
{
  "model": "qwen3:1.7b",
  "prompt_version": "gen-4",
  "repeat": 3,
  "overall": 0.8807,
  "cases": { "agentic_en_x": 0.8304, "matraix_ar_linkedin": 0.9715, ... }
}
```

A run on a different prompt version or model still compares, but says so —
a prompt change is *expected* to move scores, and treating that as an alarm
would train everyone to ignore alarms.

## What the suite found on its first run

It paid for itself immediately, which is the honest argument for building it:

- **The `hook` field failed on every case.** The model returned hashtags or a
  question about the post where the opening sentence was asked for. The fix was
  not a better prompt — it was deleting the field and computing the first
  sentence (`gen-4`). Asking a model for something you can derive is asking for
  an error the schema cannot prevent.
- **Copying**: 252 of 263 eight-word runs in one post appeared verbatim in the
  source.
- **Over-length**: 744–971 characters against X's 280 limit.

The first is fixed. The other two are open, and are limits of a 1.7B local
model rather than of the prompt — see `docs/LLM.md` for switching providers.
