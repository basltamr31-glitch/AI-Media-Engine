"""Parse stage -- turn stored documents into story candidates.

Responsibility, stated narrowly: **extract what each document says a story is.**
It does not decide whether two candidates are the same story (that is
resolution), does not fetch anything (that is enrichment) and does not rank
(that is editorial).

Three properties, mirroring the ingest stage:

* **Idempotent.** A document that already has candidates is skipped. Re-running
  is free, and a crashed run is resumed by running it again.
* **Fault-isolated.** One malformed document, or one broken parser, costs that
  document. Newsletter templates change without warning and a single bad issue
  must not stop the morning's production.
* **Auditable.** Per-source counts land in the stage ledger, so "why were there
  no stories today?" is answerable from data.

Candidates are *derived* data. Documents are never deleted, so candidates can
always be recomputed -- which is why ``--reparse`` may replace them, while
nothing may replace a document.
"""

from __future__ import annotations

from typing import Any

from engine.adapters.sources.registry import ParserRegistry
from engine.core.domain.source import Source, SourceDocument
from engine.core.domain.story import StoryCandidate
from engine.observability.logging import get_logger
from engine.pipeline.context import RunContext
from engine.pipeline.stages.base import StageError, StageResult

logger = get_logger(__name__)


class ParseStage:
    """Extracts story candidates from documents that have none."""

    def __init__(
        self,
        *,
        parsers: ParserRegistry,
        max_documents: int = 200,
        only_source: str | None = None,
        reparse: bool = False,
    ) -> None:
        self._parsers = parsers
        self._max_documents = max_documents
        self._only_source = only_source
        # Re-extraction after a parser fix. Off by default: silently rewriting
        # derived data on every run would make "what changed?" unanswerable.
        self._reparse = reparse

    @property
    def name(self) -> str:
        return "parse"

    def run(self, context: RunContext) -> StageResult:
        with context.uow as uow:
            sources = {
                source.id: source
                for source in uow.sources.list_for_workspace(
                    context.workspace_id, only_active=False
                )
            }
            selected = self._select_sources(sources.values())
            if selected is None:
                return StageResult.skip(f"no source named {self._only_source!r}")

            documents = self._load_documents(uow, context, selected)
            if not documents:
                return StageResult.skip("no documents to parse")

            already_parsed = uow.candidates.documents_with_candidates(
                context.workspace_id, [document.id for document in documents]
            )

        stats: dict[str, dict[str, Any]] = {}
        total_candidates = 0
        skipped = 0

        for document in documents:
            source = sources.get(document.source_id)
            if source is None:  # pragma: no cover -- foreign key makes this unreachable
                continue
            outcome = stats.setdefault(source.name, _empty_outcome(source))
            outcome["documents"] += 1

            if document.id in already_parsed and not self._reparse:
                outcome["already_parsed"] += 1
                skipped += 1
                continue

            # Resolved once per source, not once per document: a missing parser
            # is one configuration problem, and logging it eighty times buries
            # the problems that are not.
            parser_key = source.parser_key or ""
            if parser_key not in self._parsers:
                if outcome["error"] is None:
                    logger.warning("parse.no_parser", source=source.name, parser_key=parser_key)
                outcome["failed"] += 1
                outcome["error"] = f"no parser registered for {parser_key!r}"
                continue

            produced = self._parse_document(context, source, document, outcome)
            total_candidates += produced

        parsed = sum(int(outcome["parsed"]) for outcome in stats.values())
        failures = sum(int(outcome["failed"]) for outcome in stats.values())

        # Every attempted document failing is systemic -- a bad deployment, a
        # parser that cannot import -- and should fail the run. Partial failure
        # is a template change and must not.
        if failures and parsed == 0 and failures == len(documents) - skipped:
            details = "; ".join(
                f"{name}: {values['error']}"
                for name, values in stats.items()
                if values.get("error")
            )
            raise StageError(self.name, f"every document failed to parse: {details}")

        return StageResult(
            items_in=len(documents),
            items_out=total_candidates,
            meta={
                "sources": stats,
                "documents_considered": len(documents),
                "documents_skipped": skipped,
                "reparse": self._reparse,
                "dry_run": context.dry_run,
            },
        )

    def _select_sources(self, sources: Any) -> list[Source] | None:
        """Narrow to one source by name, as the ingest stage does."""
        listed = list(sources)
        if not self._only_source:
            return listed
        wanted = self._only_source.casefold()
        matched = [source for source in listed if source.name.casefold() == wanted]
        return matched or None

    def _load_documents(
        self, uow: Any, context: RunContext, sources: list[Source]
    ) -> list[SourceDocument]:
        """Newest documents first, restricted to the selected sources.

        Sources without a ``parser_key`` are excluded here rather than skipped
        later, so their documents never consume the document budget.
        """
        parseable = [source.id for source in sources if source.parser_key]
        if not parseable:
            return []

        if len(parseable) == 1:
            single: list[SourceDocument] = uow.documents.list_for_workspace(
                context.workspace_id, limit=self._max_documents, source_id=parseable[0]
            )
            return single

        wanted = set(parseable)
        documents: list[SourceDocument] = uow.documents.list_for_workspace(
            context.workspace_id, limit=self._max_documents
        )
        return [document for document in documents if document.source_id in wanted]

    def _parse_document(
        self,
        context: RunContext,
        source: Source,
        document: SourceDocument,
        outcome: dict[str, Any],
    ) -> int:
        """Parse one document, converting any failure into a counted outcome.

        The source is known to have a registered parser by this point.
        """
        document_logger = logger.bind(document_id=document.id, source=source.name)
        parser_key = source.parser_key or ""
        parser = self._parsers.get(parser_key)
        try:
            if not parser.can_parse(document):
                outcome["unparseable"] += 1
                document_logger.info("parse.declined", parser_key=parser_key)
                return 0
            candidates = parser.parse(document, config=source.config)
        except Exception as exc:
            outcome["failed"] += 1
            outcome["error"] = f"{type(exc).__name__}: {exc}"
            document_logger.warning("parse.failed", error=outcome["error"])
            return 0

        outcome["parsed"] += 1
        outcome["candidates"] += len(candidates)

        if context.dry_run:
            document_logger.info("parse.dry_run", candidates=len(candidates))
            return len(candidates)

        self._persist(context, document, candidates)
        document_logger.info("parse.document_finished", candidates=len(candidates))
        return len(candidates)

    def _persist(
        self, context: RunContext, document: SourceDocument, candidates: list[StoryCandidate]
    ) -> None:
        """Replace this document's candidates, in one transaction.

        Delete-then-insert rather than merge: a re-parse should leave exactly
        what the current parser produces, with no residue from the version that
        preceded it.
        """
        with context.uow as uow:
            if self._reparse:
                uow.candidates.delete_for_document(context.workspace_id, document.id)
            if candidates:
                uow.candidates.add_many(candidates)
            uow.commit()


def _empty_outcome(source: Source) -> dict[str, Any]:
    return {
        "parser": source.parser_key or "",
        "documents": 0,
        "parsed": 0,
        "candidates": 0,
        "already_parsed": 0,
        "unparseable": 0,
        "failed": 0,
        "error": None,
    }
