"""Domain pack loading port.

Filesystem today. A database or remote registry adapter later needs no caller
changes -- which is the point of loading being a port rather than an import.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.core.domain.pack import DomainPack


class PackNotFoundError(LookupError):
    """Raised when a workspace references a pack that does not exist."""


class PackLoadError(ValueError):
    """Raised when a pack exists but is malformed.

    Separate from :class:`PackNotFoundError` because the fixes differ: one means
    "check the pack key", the other means "fix line 12 of pack.yaml".
    """


@runtime_checkable
class PackLoader(Protocol):
    """Loads domain packs by key."""

    def load(self, key: str) -> DomainPack:
        """Load one pack.

        Raises:
            PackNotFoundError: If no pack with that key exists.
            PackLoadError: If the pack is present but invalid.
        """
        ...

    def list_keys(self) -> list[str]:
        """Every available pack key, sorted."""
        ...
