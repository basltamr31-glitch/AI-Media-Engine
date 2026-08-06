# 10. Source name is the natural key; config reconciles rather than inserts

Date: 2026-08-05

## Status

Accepted (supersedes the source uniqueness rule in ADR 0004's initial schema)

## Context

Phase 0 made `(workspace_id, kind, identifier)` unique on `sources`, reasoning
that one logical feed per workspace keeps re-seeding idempotent. `engine
workspace seed` matched existing rows on that key.

Running Phase 1 against live feeds broke it within minutes. arXiv answered
`http://export.arxiv.org/rss/cs.AI` with a 301 to HTTPS, so the config was
updated to the canonical URL. The next `seed`:

- did not match the existing row (different identifier),
- inserted a **second** arXiv source,
- left the original active and still being polled,
- and orphaned the 40 documents already ingested under it.

Worse, both rows carried the same `name`, and the ingest stage keys its
per-source statistics by name — so one source's numbers silently overwrote the
other's in the run ledger. The report looked normal while being wrong.

Two distinct mistakes, one root cause: **the identifier was treated as identity
when it is actually a mutable property.**

## Decision

**The name is the natural key within a workspace.** `uq_sources_workspace_id_name`
replaces the identifier constraint, which becomes a plain index.

**Seeding reconciles the full source list** rather than upserting row by row:

1. Group stored sources by name.
2. For each configured source, update the matching row in place — including its
   identifier. Where duplicates exist, keep the row carrying the most ingested
   documents (ties broken toward the oldest) and deactivate the rest.
3. Deactivate any stored source whose name is absent from the config.

**Deactivate, never delete.** Ingested documents are evidence supporting
published claims. A config edit must not destroy them.

Migration `b2c41f7d9e33` carries the data step that retires pre-existing
duplicates by suffixing their names and deactivating them, so the constraint can
be applied to a live database without data loss.

## Consequences

**Good**

- A source's URL can change — protocol upgrade, API version, query parameter —
  and its ingestion history follows it.
- Removing a source from config stops the polling and keeps the archive.
- Per-source ledger statistics cannot collide, because names are now unique by
  constraint rather than by hope.
- The same endpoint may legitimately appear under two names: one Hacker News
  query at two attention thresholds, one releases feed filtered two ways. The
  old constraint forbade this real use case.

**Costs, accepted**

- Renaming a source in config now creates a new source and deactivates the old
  one, splitting its history. Renames are rare and the old documents remain
  reachable; a `--rename old=new` flag can be added if this ever stings.
- Reconciliation reads all sources for the workspace and counts documents per
  duplicate group. Negligible at the tens-of-sources scale this operates at.

## Note

This decision cost a migration in the phase immediately after the schema was
written, which is the argument for the migration discipline established in
Phase 0 rather than an argument against it. The schema was wrong; the tooling
made it a fifteen-minute fix on a live database instead of a rebuild.
