# Rendering

A day's output becomes a folder someone can publish from.

```bash
engine render run                      # today's edition
engine render run --edition 2026-08-08 # a named one
engine render show                     # summarise the latest
```

```
output/<workspace>/<YYYY-MM-DD>/
├── package.json   canonical, machine-read
└── post.md        the human view of it
```

After this command, **nothing downstream needs a database.** That is the point
of the folder: it is the hand-off between this engine and whatever publishes.

## package.json is canonical; post.md is a view

The Markdown is derived from the package rather than assembled separately, so
the two cannot drift apart and disagree about what was produced.

The package is:

- **Self-contained.** Post text, the story, the primary source, the score that
  selected it, and the evidence behind it. Whatever reads this in a year
  probably will not have the database.
- **Deterministic.** Keys ordered, collections sorted, floats rounded.
  Re-rendering an unchanged day produces byte-identical output apart from
  `generated_at` and `run_id`, so `git diff` shows editorial change rather than
  churn.
- **Versioned.** `schema_version` is the promise to whoever parses it.

One deliberate omission: the **extracted article text is not in the package.**
It is somebody else's copyright, and a publishing artifact is the wrong place
to redistribute it. The URL and a character count are there instead.

## The edition date is local, not UTC

An edition is a *publishing day*. A workspace in `Asia/Riyadh` rolls over three
hours before UTC does, so a late-evening batch filed by UTC date would land
under tomorrow — the kind of bug nobody notices until someone asks where
Tuesday went. The date comes from the workspace's own timezone; an unparseable
one falls back to UTC with a warning rather than stopping the edition.

## Re-rendering overwrites

An edition is derived data and can always be rebuilt, so keeping stale copies
would only create two answers to the same question. The revision chain, which
is *not* reconstructible, is never touched by rendering.

## Arabic: why the HTML

Markdown has no way to state text direction. Every renderer infers it using
Unicode UAX#9 rules P2/P3 — *the first strong character wins* — because
Markdown gives it nothing better to go on.

That inference is wrong exactly when it matters. An Arabic paragraph opening
with a Latin product name:

> `MatrAIx يُقدم 1,010 مهام تطبيقية تشمل أكثر من 25 مجالًا.`

has `M` as its first strong character, so it is laid out **left-to-right**: the
full stop moves to the wrong side and any parenthetical reorders. The result is
not "slightly off" — it is wrong to an Arabic reader, and it looks perfectly
fine to everyone else, so it ships.

So each Arabic paragraph is emitted as:

```html
<p dir="rtl" align="right">…</p>
```

HTML is legal in Markdown and `dir` is honoured by GitHub, VS Code, Obsidian
and every browser — this is the portable answer, not a GitHub trick. Direction
follows the **language tag**, not the characters: a release note thick with
Latin product names is still an Arabic post.

Left-to-right text is emitted as plain Markdown. Wrapping it in HTML would only
stop emphasis and links from rendering.

A test computes UAX#9 P2/P3 directly and asserts that the same sentence
inferred two different ways still renders with one explicit direction — so the
layout no longer depends on which word the model happened to start with.

`package.json` is written with `ensure_ascii=False`, so Arabic is readable in
the file rather than escaped into `\uXXXX`.

## What is deliberately not here

- **No publishing.** Rendering writes files; sending them anywhere is Phase 5.
- **No per-channel files.** One `post.md` covers every channel and language,
  because the reader is a person deciding what to publish, not a bot.
- **No HTML output.** Markdown renders everywhere that matters and stays
  diffable; an HTML build belongs with a site, not with the engine.
