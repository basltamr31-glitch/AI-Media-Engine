"""Tests for the raw payload archive."""

from __future__ import annotations

import json
from pathlib import Path

from engine.adapters.sources.archive import FileSystemRawArchive, NullRawArchive
from tests.fakes import make_document


def build_document(clock, *, content_hash: str = "abc123", raw_content: str = "<h1>Hi</h1>"):
    return make_document(
        workspace_id="ws_1",
        source_id="src_1",
        content_hash=content_hash,
        fetched_at=clock.now(),
        raw_content=raw_content,
    )


class TestFileSystemRawArchive:
    def test_writes_a_date_partitioned_file(self, tmp_path, clock):
        archive = FileSystemRawArchive(tmp_path)
        document = build_document(clock)

        path = archive.store(document, workspace_slug="default")

        assert "2026-01-15" in path
        assert document.id in path
        assert (tmp_path / "default" / "2026-01-15" / f"{document.id}.json").is_file()

    def test_envelope_is_self_describing(self, tmp_path, clock):
        """An archived file must be replayable without the database."""
        archive = FileSystemRawArchive(tmp_path)
        document = build_document(clock)

        path = archive.store(document, workspace_slug="default")
        stored = json.loads(Path(path).read_text(encoding="utf-8"))
        assert stored["id"] == document.id
        assert stored["content_hash"] == document.content_hash
        assert stored["raw_content"] == document.raw_content
        assert stored["source_id"] == document.source_id

    def test_preserves_non_latin_content_readably(self, tmp_path, clock):
        """Arabic must survive as Arabic, not as escaped \\uXXXX sequences."""
        archive = FileSystemRawArchive(tmp_path)
        document = build_document(clock, raw_content="<p>الذكاء الاصطناعي</p>")

        path = archive.store(document, workspace_slug="default")
        text = (tmp_path / "default" / "2026-01-15" / f"{document.id}.json").read_text("utf-8")

        assert "الذكاء الاصطناعي" in text
        assert json.loads(text)["raw_content"] == document.raw_content
        assert path.endswith(".json")

    def test_sanitises_the_workspace_segment(self, tmp_path, clock):
        archive = FileSystemRawArchive(tmp_path)
        archive.store(build_document(clock), workspace_slug="Acme Corp/../etc")
        # The traversal attempt collapses into a single safe directory name.
        assert not (tmp_path / "etc").exists()
        assert len(list(tmp_path.iterdir())) == 1

    def test_two_documents_do_not_collide(self, tmp_path, clock):
        archive = FileSystemRawArchive(tmp_path)
        archive.store(build_document(clock, content_hash="one"), workspace_slug="default")
        archive.store(build_document(clock, content_hash="two"), workspace_slug="default")
        assert len(list((tmp_path / "default" / "2026-01-15").iterdir())) == 2


class TestNullRawArchive:
    def test_writes_nothing_and_stays_silent(self, tmp_path, clock):
        assert NullRawArchive().store(build_document(clock), workspace_slug="default") == ""
        assert list(tmp_path.iterdir()) == []
