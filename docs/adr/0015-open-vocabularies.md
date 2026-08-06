# ADR 0015: Enum what the engine controls; table what the market controls

- **Status:** Accepted (supersedes the `Channel`, `ContentKind` and `Language`
  enums introduced in Phase 0)
- **Date:** Phase 1.5

## Context

Phase 0 defined several `StrEnum`s. Reviewing them at the end of Phase 1, they
fall into two distinct groups that were not distinguished at the time.

**Group one — pipeline mechanics.** `StoryStatus`, `RunStatus`, `StageStatus`,
`ContentState`, `EvidenceKind`. These describe how the engine works. The engine
defines them, the engine exhausts them, and a new value implies new engine code
anyway. They are genuine closed sets.

**Group two — market surface.** `Channel` (Instagram, X, LinkedIn), `ContentKind`
(social post, caption, hashtags, reel script, voiceover, image prompt),
`Language` (ar, en). These enumerate *products in a market we do not control*.

The consequences of getting group two wrong:

- Adding Threads, Bluesky, Telegram, a newsletter or a Slack digest requires a
  code change, a migration and a redeploy. Meanwhile **adding a news source is
  YAML** (ADR 0009). Sources are plugins; channels are hardcoded. That is an
  inconsistency, not a design.
- `ContentKind` is shaped like social media. A research lab wants a literature
  digest; a compliance team wants an advisory brief. Neither has hashtags. Both
  need a code change to exist — which fails the three-customer test in
  `STRATEGY.md`.
- `Language` with two members means a German finance customer needs a migration.

Notably, Phase 0 already solved this problem correctly *once*: `SourceKind` is a
**transport** (email/rss/api) with provider-specific behaviour hanging off a data
key (`parser_key`). That is why Reddit is configuration. The same pattern was
simply not applied to the output side.

## Decision

**Enum what the engine controls. Table what the market controls.**

Group one stays as enums, unchanged.

Group two becomes pack-defined vocabulary:

| Was | Becomes | Defined in |
|---|---|---|
| `Channel` enum | `ChannelDef` row: `key, name, max_chars, supports_*, format_rules, renderer_key` | pack |
| `ContentKind` enum | `ProductDef` row: `key, name, kind, schema, renderer_key` | pack |
| `Language` enum | BCP-47 string, *validated* not enumerated | — |

`ProductDef.kind` is a small closed enum (`post`, `digest`, `alert`, `report`,
`dataset`) because it selects engine behaviour — how the product is rendered and
delivered. The *specific product* is data; the *class of thing* is mechanics.
This is the same split as `SourceKind` versus `parser_key`.

**Storage does not change.** These enums already persist as strings, so the
columns are untouched. What moves is where validation happens: from a Python
enum to a pack lookup. That makes this a low-risk change today and an expensive
one after content tables have rows referencing the values.

**Language becomes a validated BCP-47 tag.** Not a free string — `"english"` and
`"EN-gb"` must not both be storable. A tag validator plus per-workspace declared
languages gives correctness without enumeration.

## Consequences

**Good**

- Adding a channel or an output product is a pack edit, matching how sources
  already work. The system becomes internally consistent.
- Customer B (research monitoring, no social output) becomes expressible without
  a code branch.
- Any language, with validation.
- Channel formatting rules live beside the channel definition instead of in a
  `ChannelSpec` file that has to be kept in sync with an enum.

**Costs, accepted**

- Loss of exhaustiveness checking. `match` over `Channel` no longer proves all
  cases are handled. Mitigated: the places that branched on channel are now
  renderer plugins selected by `renderer_key`, which is a lookup failure rather
  than a silent fall-through.
- A typo in a pack becomes a runtime error rather than a type error. Mitigated by
  schema validation on pack load and `engine pack validate` in CI.
- Slightly more indirection for a reader: "what channels exist?" is answered by
  `engine pack show`, not by reading an enum.

## Alternatives considered

**Keep the enums and extend them as needed.** Honest and simple, and it is what
most projects do. Rejected because every extension is a migration touching
`content_assets`, `publications` and `performance_metrics`, and because it makes
the vertical-agnosticism claim in ADR 0014 undeliverable.

**Free strings with no validation.** Maximum flexibility, no integrity. `"linkedin"`,
`"LinkedIn"` and `"linked-in"` would all be storable and would silently fragment
every per-channel analytic.

**A registry in code rather than in packs.** A `CHANNELS: dict[str, ChannelDef]`
module is better than an enum and worse than a pack: it still requires a deploy,
and it cannot differ per tenant, which is the whole point.
