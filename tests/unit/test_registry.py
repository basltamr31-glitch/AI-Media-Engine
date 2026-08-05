"""Source registry behaviour -- the extension point for new newsletters."""

from __future__ import annotations

import pytest

from engine.adapters.sources.registry import (
    FetcherRegistry,
    ParserNotFoundError,
    ParserRegistry,
)
from engine.core.domain.enums import SourceKind
from engine.core.domain.source import SourceDocument
from engine.core.domain.story import StoryCandidate


class FakeParser:
    def __init__(self, key: str) -> None:
        self._key = key

    @property
    def parser_key(self) -> str:
        return self._key

    def can_parse(self, document: SourceDocument) -> bool:
        return True

    def parse(self, document: SourceDocument) -> list[StoryCandidate]:
        return []


class FakeFetcher:
    def __init__(self, kind: SourceKind) -> None:
        self._kind = kind

    @property
    def kind(self) -> SourceKind:
        return self._kind

    def fetch(self, source: object, *, limit: int = 50) -> list[SourceDocument]:
        return []


class TestParserRegistry:
    def test_register_and_retrieve(self):
        registry = ParserRegistry()
        registry.register(FakeParser("tldr_newsletter"))
        assert registry.get("tldr_newsletter").parser_key == "tldr_newsletter"
        assert "tldr_newsletter" in registry
        assert len(registry) == 1

    def test_duplicate_registration_is_rejected(self):
        """Silent overwrites would make parser selection order-dependent."""
        registry = ParserRegistry()
        registry.register(FakeParser("dup"))
        with pytest.raises(ValueError, match="already registered"):
            registry.register(FakeParser("dup"))

    def test_explicit_replacement_is_allowed(self):
        registry = ParserRegistry()
        registry.register(FakeParser("dup"))
        registry.register(FakeParser("dup"), replace=True)
        assert len(registry) == 1

    def test_unknown_key_lists_alternatives(self):
        registry = ParserRegistry()
        registry.register(FakeParser("arxiv_rss"))
        with pytest.raises(ParserNotFoundError, match="available: arxiv_rss"):
            registry.get("nope")

    def test_phase_zero_registry_is_empty(self):
        """No parsers exist yet by design -- only the seam they will plug into."""
        assert len(ParserRegistry()) == 0


class TestFetcherRegistry:
    def test_register_by_transport_kind(self):
        registry = FetcherRegistry()
        registry.register(FakeFetcher(SourceKind.RSS))
        assert registry.get("rss").kind is SourceKind.RSS

    def test_duplicate_kind_is_rejected(self):
        registry = FetcherRegistry()
        registry.register(FakeFetcher(SourceKind.EMAIL))
        with pytest.raises(ValueError, match="already registered"):
            registry.register(FakeFetcher(SourceKind.EMAIL))
