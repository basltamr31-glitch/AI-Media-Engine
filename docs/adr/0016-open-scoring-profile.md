# ADR 0016: Ranking signals are plugins; weights are an open map

- **Status:** Accepted (supersedes `ScoringWeights` as defined in Phase 0)
- **Date:** Phase 1.5

## Context

`ScoringWeights` declares five named float fields — `corroboration`,
`source_authority`, `recency`, `prominence`, `audience_fit` — with a validator
requiring they sum to 1.0. It inherits `DomainModel`, which sets
`extra="forbid"`.

That last detail is the problem. A workspace YAML adding

```yaml
weights:
  community_attention: 0.15
```

**raises a validation error.** To add one ranking signal you must edit the domain
model, edit the validator, migrate every workspace config, and redeploy.

Phase 2 already needs at least two signals that do not exist (community
attention, and the technical/business impact split). More importantly, the
signals a *vertical* needs are not the signals AI news needs:

| Vertical | Signals that matter |
|---|---|
| AI news | corroboration, community attention, recency |
| Cybersecurity | CVSS severity, exploit-in-the-wild, affected install base |
| Pharma | trial phase, sample size, endpoint significance |
| Finance | market cap, earnings-surprise magnitude, analyst dispersion |

None of those can exist without a code change. "One ranking engine should
evaluate many sources" is not deliverable against a fixed five-field model.

## Decision

Split the fixed model into three pieces.

**`SignalDef`** — pack data declaring that a signal exists:
`key, kind (numeric/boolean/set), normaliser, default_weight, description`.

**`SignalComputer`** — a port with one method, `compute(story, ctx) -> float`,
registered by key through the plugin mechanism (ADR 0017). One computer per
signal, independently testable, independently cacheable.

**`ScoringProfile`** — an **open** `dict[str, float]`, validated at load time
against the signal keys the active pack registers, still required to sum to 1.0.

Computed values are persisted as `SignalValue` rows —
`(story_id, signal_key, raw, normalised, signal_version, computed_at)` — not
merely summed into a total.

That last point is the one that matters most and is easy to skip. Storing signals
separately gives a **feature matrix per story**, which is exactly the training
input for fitting weights against real engagement in Phase 5. Keeping only the
scalar total makes weight learning impossible, because the inputs are gone.

It also separates two things with very different cost profiles: computing signals
is expensive and cacheable; weighting them is cheap and changes constantly.
Re-ranking six months of history with new weights becomes a pure function over
stored rows, with no recomputation and no I/O — which is what makes weight
experiments and A/B tests affordable.

## Consequences

**Good**

- A new ranking signal is a pack entry plus a small plugin, with no change to the
  ranking framework.
- Each vertical ranks on what its buyers actually care about.
- Weights become learnable, because their inputs are stored.
- Re-ranking is free, enabling experimentation on historical data.
- `EditorialScore.components` already stores per-signal contributions, so
  explainability needs no further change.

**Costs, accepted**

- Weight typos are caught at pack-load time rather than by the type checker.
  Acceptable: the validator now checks keys against registered signals, which is
  a stronger guarantee than a field name — it catches a signal referenced but
  never implemented.
- One more table, growing at (stories × signals). Small, and prunable by age.
- Slightly more machinery than five fields for a system that today has seven
  signals. Justified by Phase 5 depending entirely on it.

## Alternatives considered

**Keep the five fields and add new ones as needed.** Each addition is a code
change plus a config migration, and it forecloses per-vertical ranking. It also
means the intelligence layer — the most valuable code we will write — gets built
against a model we know is wrong.

**A single opaque `weights: dict` with no validation.** Flexible and undebuggable.
A misspelled key would silently contribute zero, and a ranking that is quietly
wrong is worse than one that fails loudly.

**Compute signals inline during ranking, without storing them.** Simpler, one
fewer table. Rejected because it makes weight learning impossible and forces a
full recompute for every experiment. The stored feature matrix *is* the asset.

**Learn the weights from the start (a small model instead of hand-set weights).**
No training data exists yet — that is what Phase 3 and 4 accumulate. Hand-set,
explainable weights are the correct starting point, and the schema is designed so
fitted weights can replace them without a structural change.
