"""Filesystem archive for verbatim fetched payloads.

Layout::

    data/raw/<workspace-slug>/<YYYY-MM-DD>/<document-id>.json

Date-partitioned so a day's intake can be inspected, zipped as a parser fixture
set, or pruned by retention policy without touching the database. Document IDs
are ULID-based and filesystem-safe by construction, so no path sanitisation
gymnastics are required.

One file per document rather than an append-only log: individual documents need
to be read back independently during Phase 2 parser development, and a corrupt
write damages exactly one record.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Final

from engine.core.domain.source import SourceDocument
from engine.observability.logging import get_logger

logger = get_logger(__name__)

_UNSAFE_SEGMENT: Final = re.compile(r"[^a-z0-9._-]+")


def _safe_segment(value: str) -> str:
    """Reduce a path segment to characters safe on every filesystem."""
    cleaned = _UNSAFE_SEGMENT.sub("-", value.strip().lower()).strip("-")
    return cleaned or "unknown"


class FileSystemRawArchive:
    """Writes documents to disk, one JSON file each.

    Implements :class:`engine.core.ports.sources.RawDocumentArchive`.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def store(self, document: SourceDocument, *, workspace_slug: str) -> str:
        """Write ``document`` verbatim and return the file path.

        The stored envelope carries the metadata alongside the payload, so an
        archived file is self-describing and replayable without the database.
        """
        directory = (
            self._root / _safe_segment(workspace_slug) / document.fetched_at.strftime("%Y-%m-%d")
        )
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{document.id}.json"

        envelope = {
            "id": document.id,
            "workspace_id": document.workspace_id,
            "source_id": document.source_id,
            "external_id": document.external_id,
            "content_hash": document.content_hash,
            "title": document.title,
            "author": document.author,
            "url": document.url,
            "content_type": document.content_type,
            "published_at": document.published_at.isoformat() if document.published_at else None,
            "fetched_at": document.fetched_at.isoformat(),
            "meta": document.meta,
            "raw_content": document.raw_content,
        }

        # ensure_ascii=False keeps Arabic and other non-Latin content readable
        # in the archive rather than escaped into unreadable \uXXXX sequences.
        destination.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.debug("archive.stored", document_id=document.id, path=str(destination))
        return str(destination)


class NullRawArchive:
    """No-op archive used when archiving is disabled or during a dry run.

    A null object rather than an ``if archive is not None`` branch at every call
    site: the stage stays linear and the disabled path is exercised by the same
    code as the enabled one.
    """

    def store(self, document: SourceDocument, *, workspace_slug: str) -> str:
        return ""
