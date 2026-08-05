# ADR 0002: One distribution with machine-enforced layers

- **Status:** Accepted
- **Date:** Phase 0

## Context

The approved architecture described a monorepo of four separately installable
packages — `engine_core`, `engine_adapters`, `engine_pipeline`, `engine_evals` —
with `apps/` for the CLI and future API. The goal was hard architectural
boundaries: the domain must not import infrastructure.

That goal is correct and non-negotiable. The question is what mechanism enforces
it.

Separate distributions enforce boundaries through packaging: `engine_core`'s
`pyproject.toml` simply does not depend on SQLAlchemy, so importing it fails.
The cost is four `pyproject.toml` files, four editable installs, cross-package
version coordination, and a build graph to keep in sync — for a single developer
who will spend the next several months writing features, not packaging.

## Decision

Ship **one** distribution, `ai-media-engine`, containing `src/engine/` with
sub-packages that mirror the approved boundaries exactly:

```
engine.core           engine.pipeline
engine.adapters       engine.observability
engine.cli            engine.settings
```

Enforce the boundaries with [import-linter](https://import-linter.readthedocs.io)
in `.importlinter`, checked via `lint-imports`:

1. A **layers** contract: dependencies point inward only.
2. A **forbidden** contract: `engine.core` may not import `sqlalchemy`,
   `alembic`, `typer`, `rich`, `structlog`, `yaml`, `anthropic` or `httpx`.

A violation fails the check and, later, CI.

## Alternatives considered

**Four distributions as originally specified.** Strongest possible enforcement.
Rejected for setup friction disproportionate to a solo project at this stage —
the enforcement gain over import-linter is marginal, while the daily cost is not.

**Convention and code review only.** Zero tooling. Rejected: unenforced layering
always decays, usually under deadline pressure, and this codebase's value depends
on the domain staying portable.

## Consequences

**Positive**

- Same logical architecture, same enforcement strength in practice, one install.
- Refactoring across layers is a single-repo operation with no version dance.
- Boundary violations are caught mechanically rather than in review.

**Negative**

- Enforcement is a tool that must be run, not a physical impossibility. Mitigated
  by making `lint-imports` part of the standard check sequence and CI.
- A future consumer wanting only the domain must depend on the whole
  distribution.

**Reversibility: high.** Splitting later is a mechanical move — the import graph
already proves nothing crosses a boundary illegally, which is the hard part of
any such split. Expected trigger: a second application (worker or API) needing
independent deployment cadence, likely around Phase 7.
