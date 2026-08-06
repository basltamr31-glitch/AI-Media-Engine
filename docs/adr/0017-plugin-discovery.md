# ADR 0017: Plugins register through entry points, not imports

- **Status:** Accepted
- **Date:** Phase 1.5

## Context

Phase 1 established registries — `FetcherRegistry`, `ParserRegistry` — and the
composition root populates them by importing concrete classes and calling
`register()`. Phase 2 adds more: signal computers, similarity scorers, entity
resolvers, renderers.

That works, and it has a ceiling: **every plugin must live in this repository.**
Adding a YouTube transcript transport, a podcast source or a Telegram destination
means a pull request against the core engine, a version bump and a redeploy.

The stated goal is different: someone should be able to install a package and
have it extend the engine without touching existing code. A source list of
YouTube, podcasts, PDFs, Product Hunt, government feeds and whatever comes next
is not a list one team maintains in one repository.

There is also a nearer-term reason. Verticals will need domain-specific
computers — a CVSS severity signal for cybersecurity, a trial-phase signal for
pharma. Those belong beside their pack, not in the engine.

## Decision

Registries discover implementations through **`importlib.metadata` entry
points**, the standard Python mechanism used by pytest, flake8, Airflow and
setuptools itself.

A third-party package declares:

```toml
[project.entry-points."engine.fetchers"]
youtube = "engine_youtube.fetcher:YouTubeFetcher"

[project.entry-points."engine.signals"]
cvss_severity = "engine_secpack.signals:CvssSeverity"
```

Groups: `engine.fetchers`, `engine.parsers`, `engine.signals`,
`engine.renderers`, `engine.destinations`, `engine.packs`.

Four rules make this safe rather than merely clever:

**Built-ins register the same way.** The engine's own fetchers are declared in
its own `pyproject.toml`. If the mechanism breaks, it breaks in development, not
only for third parties.

**Discovery failures are isolated and loud.** A plugin that raises on import is
logged and skipped; it does not prevent the engine from starting. One broken
third-party package must not take down a customer's pipeline.

**Plugins declare a manifest** — name, version, kind, config JSON-schema,
capability declarations. This makes validation possible now and a marketplace UI
possible later without redesign.

**Discovery is inspectable.** `engine plugins list` shows what was found, what
loaded, and what failed and why. An extension mechanism nobody can debug is worse
than no extension mechanism.

## Consequences

**Good**

- Third-party sources, destinations and signals without a core change — the
  Phase 8 ecosystem becomes a packaging question rather than an architectural one.
- Domain-specific signal computers ship beside their pack.
- Optional heavy dependencies (a transcription library, a PDF parser) stay
  optional, installed only by those who need them.
- The engine's own registries get exercised through the public extension path,
  so it cannot rot.

**Costs, accepted**

- Entry-point scanning adds a few milliseconds to startup. Cached per process.
- Installing a plugin means executing third-party code with full engine
  privileges. This is true of every Python plugin system; sandboxing is a
  Phase 7+ concern tied to hosted multi-tenancy, and until then plugins are
  installed deliberately by the operator.
- Debugging "why is this fetcher not registered?" is less obvious than reading
  the composition root. Mitigated by `engine plugins list` and by keeping
  built-ins explicit in `pyproject.toml` where they are readable.

## Alternatives considered

**Keep explicit imports in the composition root.** Simplest, fully static,
completely inspectable — and it caps the ecosystem at what this repository
contains. Rejected on the stated goal.

**A plugin directory scanned at runtime** (drop a `.py` file into `plugins/`).
No packaging, no versioning, no dependency resolution, no way to publish or
install. Entry points give all four for free.

**A configuration file listing dotted paths to import.** Halfway between: it
avoids code changes but still requires the operator to know and maintain the
list, and it cannot express dependencies. Entry points make installation
sufficient.

**Defer until Phase 8.** The registries exist *now* and Phase 2 adds four more.
Making them entry-point-aware costs roughly sixty lines today; retrofitting
discovery into six registries and their tests later costs considerably more, and
in the meantime every new registry is built the wrong way.
