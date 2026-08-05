# ADR 0004: SQLite locally, Postgres-compatible schema

- **Status:** Accepted
- **Date:** Phase 0

## Context

The system needs durable storage now (documents, stories, ledger) and will need
a hosted, concurrent database when it becomes multi-tenant.

Running Postgres in Docker from day one is the "production parity" answer, but it
adds a daemon, a compose file and a Docker dependency to a Windows development
machine — friction paid on every single run, in exchange for a benefit that
arrives in roughly a year.

The real risk is not which database is used locally; it is accumulating
SQLite-specific assumptions that make the eventual migration painful.

## Decision

Use **SQLite locally**, with the schema deliberately restricted to a
**Postgres-compatible subset**, and access it exclusively through SQLAlchemy with
Alembic migrations.

Concretely:

- JSON columns declared as `JSON().with_variant(JSONB(), "postgresql")` — plain
  JSON on SQLite, indexable binary JSON on Postgres, automatically.
- Every constraint explicitly named via a metadata naming convention. Alembic
  cannot reliably alter unnamed constraints, and SQLite requires table rebuilds
  (`render_as_batch`) for most alterations.
- Enums stored as `VARCHAR`, not native database enums, so adding a value is not
  a migration.
- All timestamps `DateTime(timezone=True)`, stored in UTC.
- Foreign keys enabled explicitly (`PRAGMA foreign_keys=ON`) — SQLite ignores
  them by default, which would let the tenant hierarchy grow orphan rows
  silently.
- WAL journal mode, so an inspection command can read while a pipeline writes.
- No SQLite-only functions, types or behaviour anywhere.

Switching to Postgres is then one line:

```
ENGINE_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/engine
```

## Alternatives considered

**Postgres in Docker now.** Best parity, and defensible. Rejected for Phase 0
friction; reconsider at Phase 6 when concurrency and hosted deployment become
real requirements.

**JSON or Markdown files, no database.** Simplest possible start. Rejected: the
feedback loop, provenance ledger and run history are all relational and all
required early. Files would be rewritten within two phases.

**An ORM-free approach with raw SQL.** More control, but hand-rolling migrations
and mapping is a poor use of time when the schema will change every phase.

## Consequences

**Positive**

- Zero setup friction: no daemon, no Docker, works offline.
- Migration to Postgres is a URL change plus `pip install ".[postgres]"`.
- Tests run against a real database — catching constraint violations and mapping
  errors that mocks hide — with per-test isolation via `tmp_path`.

**Negative**

- SQLite's single-writer model will not support concurrent pipeline runs. This is
  correct for now (one workspace, one daily run) and is precisely the signal that
  it is time to migrate.
- Postgres-only features (full-text search, `JSONB` operators, partial indexes)
  are unavailable until the switch. None are needed before Phase 6.
- Discipline is required to avoid SQLite-specific shortcuts. Mitigated by making
  the constraint explicit here and in the module docstring.
