"""Parser registry -- the extension point for new sources.

Adding a newsletter is: write a parser, register it under a key, add a config
row referencing that key. No pipeline code changes, no conditionals keyed on
sender address, and no possibility of one source's quirks leaking into another.

The registry is empty in Phase 0. The seam exists first so Phase 1 has somewhere
to plug into rather than inventing structure under deadline.
"""

from __future__ import annotations

from engine.core.ports.sources import DocumentParser, SourceFetcher


class ParserNotFoundError(LookupError):
    """Raised when a source references a parser key that is not registered."""


class ParserRegistry:
    """Maps ``Source.parser_key`` to a parser implementation."""

    def __init__(self) -> None:
        self._parsers: dict[str, DocumentParser] = {}

    def register(self, parser: DocumentParser, *, replace: bool = False) -> None:
        """Register ``parser`` under its own ``parser_key``.

        Raises:
            ValueError: If the key is already taken and ``replace`` is False.
                Silent overwrites would make parser selection order-dependent.
        """
        key = parser.parser_key
        if key in self._parsers and not replace:
            msg = f"parser key {key!r} is already registered"
            raise ValueError(msg)
        self._parsers[key] = parser

    def get(self, key: str) -> DocumentParser:
        try:
            return self._parsers[key]
        except KeyError as exc:
            available = ", ".join(sorted(self._parsers)) or "<none>"
            msg = f"no parser registered for {key!r}; available: {available}"
            raise ParserNotFoundError(msg) from exc

    def keys(self) -> list[str]:
        return sorted(self._parsers)

    def __contains__(self, key: object) -> bool:
        return key in self._parsers

    def __len__(self) -> int:
        return len(self._parsers)


class FetcherRegistry:
    """Maps a :class:`SourceKind` to the fetcher that handles that transport."""

    def __init__(self) -> None:
        self._fetchers: dict[str, SourceFetcher] = {}

    def register(self, fetcher: SourceFetcher, *, replace: bool = False) -> None:
        key = str(fetcher.kind)
        if key in self._fetchers and not replace:
            msg = f"fetcher for kind {key!r} is already registered"
            raise ValueError(msg)
        self._fetchers[key] = fetcher

    def get(self, kind: str) -> SourceFetcher:
        try:
            return self._fetchers[kind]
        except KeyError as exc:
            available = ", ".join(sorted(self._fetchers)) or "<none>"
            msg = f"no fetcher registered for kind {kind!r}; available: {available}"
            raise ParserNotFoundError(msg) from exc

    def keys(self) -> list[str]:
        return sorted(self._fetchers)

    def __contains__(self, key: object) -> bool:
        return key in self._fetchers

    def __len__(self) -> int:
        return len(self._fetchers)
