"""Ingestion stage -- the first real work the pipeline does.

Responsibility, stated narrowly on purpose: **turn configured sources into
stored documents, exactly once each.** It does not parse, rank, or interpret
anything. Every stage that tries to do two jobs becomes the stage nobody wants
to touch.

Three properties this stage guarantees:

* **Idempotent.** Re-running ingests nothing new. Content hashes are checked
  before insert, so a crashed run is resumed by simply running it again.
* **Fault-isolated.** One source failing does not deprive the pipeline of the
  others. A newsletter outage should degrade quality, not stop production.
* **Auditable.** Per-source counts land in the stage ledger, so "why was today
  thin?" is answerable from data rather than from memory.
"""

from __future__ import annotations

from typing import Any

from engine.adapters.sources.registry import FetcherRegistry
from engine.core.domain.source import Source, SourceDocument
from engine.core.ports.sources import RawDocumentArchive
from engine.observability.logging import get_logger
from engine.pipeline.context import RunContext
from engine.pipeline.stages.base import StageError, StageResult

logger = get_logger(__name__)


class IngestStage:
    """Fetches from every active source and persists new documents."""

    def __init__(
        self,
        *,
        fetchers: FetcherRegistry,
        archive: RawDocumentArchive,
        max_documents: int = 50,
        only_source: str | None = None,
    ) -> None:
        self._fetchers = fetchers
        self._archive = archive
        self._max_documents = max_documents
        # Lets the operator re-run one misbehaving source without re-fetching
        # everything -- the difference between a 2-second and a 2-minute debug loop.
        self._only_source = only_source

    @property
    def name(self) -> str:
        return "ingest"

    def run(self, context: RunContext) -> StageResult:
        with context.uow as uow:
            workspace = uow.workspaces.get(context.workspace_id)
            if workspace is None:
                msg = f"workspace {context.workspace_id} not found"
                raise StageError(self.name, msg)
            sources = uow.sources.list_for_workspace(context.workspace_id, only_active=True)

        if self._only_source:
            wanted = self._only_source.casefold()
            sources = [s for s in sources if s.name.casefold() == wanted]
            if not sources:
                return StageResult.skip(f"no active source named {self._only_source!r}")

        if not sources:
            return StageResult.skip("workspace has no active sources")

        stats: dict[str, dict[str, Any]] = {}
        seen_hashes: set[str] = set()
        total_fetched = 0
        total_stored = 0
        failures = 0

        for source in sources:
            outcome = self._ingest_source(context, workspace.slug, source, seen_hashes)
            stats[source.name] = outcome
            total_fetched += int(outcome["fetched"])
            total_stored += int(outcome["stored"])
            if outcome.get("error"):
                failures += 1

        # Every source failing is a systemic problem -- no network, expired
        # credentials, wrong configuration -- and should fail the run loudly.
        # A partial failure is normal operations and must not.
        if failures == len(sources):
            details = "; ".join(
                f"{name}: {values['error']}"
                for name, values in stats.items()
                if values.get("error")
            )
            raise StageError(self.name, f"all {failures} source(s) failed: {details}")

        return StageResult(
            items_in=total_fetched,
            items_out=total_stored,
            meta={
                "sources": stats,
                "source_count": len(sources),
                "failed_sources": failures,
                "dry_run": context.dry_run,
            },
        )

    def _ingest_source(
        self,
        context: RunContext,
        workspace_slug: str,
        source: Source,
        seen_hashes: set[str],
    ) -> dict[str, Any]:
        """Fetch and persist one source, converting any failure into a result."""
        source_logger = logger.bind(source=source.name, kind=str(source.kind))
        outcome: dict[str, Any] = {
            "kind": str(source.kind),
            "fetched": 0,
            "stored": 0,
            "duplicates": 0,
            "error": None,
        }

        if str(source.kind) not in self._fetchers:
            outcome["error"] = f"no fetcher registered for kind {source.kind}"
            source_logger.warning("ingest.no_fetcher")
            return outcome

        try:
            fetcher = self._fetchers.get(str(source.kind))
            documents = fetcher.fetch(source, limit=self._max_documents)
        except Exception as exc:
            outcome["error"] = f"{type(exc).__name__}: {exc}"
            source_logger.warning("ingest.source_failed", error=outcome["error"])
            return outcome

        outcome["fetched"] = len(documents)

        if context.dry_run:
            source_logger.info("ingest.dry_run", fetched=len(documents))
            return outcome

        stored, duplicates = self._persist(context, workspace_slug, documents, seen_hashes)
        outcome["stored"] = stored
        outcome["duplicates"] = duplicates
        source_logger.info("ingest.source_finished", stored=stored, duplicates=duplicates)
        return outcome

    def _persist(
        self,
        context: RunContext,
        workspace_slug: str,
        documents: list[SourceDocument],
        seen_hashes: set[str],
    ) -> tuple[int, int]:
        """Store new documents, skipping anything already ingested."""
        stored = 0
        duplicates = 0

        with context.uow as uow:
            for document in documents:
                # In-run set first: two sources can surface the same item, and
                # the database check alone would not catch it until commit.
                if document.content_hash in seen_hashes:
                    duplicates += 1
                    continue
                if uow.documents.exists_by_hash(document.workspace_id, document.content_hash):
                    duplicates += 1
                    seen_hashes.add(document.content_hash)
                    continue

                # Archive before insert: an orphaned file is harmless, whereas a
                # database row pointing at a file that was never written is not.
                self._archive.store(document, workspace_slug=workspace_slug)
                uow.documents.add(document)
                seen_hashes.add(document.content_hash)
                stored += 1

            uow.commit()

        return stored, duplicates
