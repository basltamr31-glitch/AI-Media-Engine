"""Filesystem-backed domain pack loader.

Layout::

    packs/
      ai-news/
        pack.yaml

One directory per pack keeps a pack self-contained, so it can be zipped, copied
between installs, or later published, without hunting for scattered files.

Packs are cached after first load. They are read-only configuration that does
not change during a run, and re-parsing YAML on every classification would be
a silly cost.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from engine.core.domain.pack import DomainPack
from engine.core.ports.packs import PackLoadError, PackNotFoundError
from engine.observability.logging import get_logger

logger = get_logger(__name__)

PACK_MANIFEST = "pack.yaml"


class FilesystemPackLoader:
    """Loads packs from a directory. Implements :class:`PackLoader`."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache: dict[str, DomainPack] = {}

    @property
    def root(self) -> Path:
        return self._root

    def load(self, key: str) -> DomainPack:
        if key in self._cache:
            return self._cache[key]

        manifest = self._root / key / PACK_MANIFEST
        if not manifest.is_file():
            available = ", ".join(self.list_keys()) or "<none>"
            msg = f"pack {key!r} not found at {manifest}; available: {available}"
            raise PackNotFoundError(msg)

        pack = self._parse(manifest, key)
        self._cache[key] = pack
        logger.debug("pack.loaded", pack=pack.identifier, taxonomy_nodes=len(pack.taxonomy))
        return pack

    def list_keys(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(
            entry.name
            for entry in self._root.iterdir()
            if entry.is_dir() and (entry / PACK_MANIFEST).is_file()
        )

    def clear_cache(self) -> None:
        """Drop cached packs. Used by tests and by `engine pack validate`."""
        self._cache.clear()

    def _parse(self, manifest: Path, key: str) -> DomainPack:
        try:
            raw: Any = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            msg = f"pack {key!r} has invalid YAML at {manifest}: {exc}"
            raise PackLoadError(msg) from exc

        if not isinstance(raw, dict):
            msg = f"pack {key!r} at {manifest} must be a mapping, got {type(raw).__name__}"
            raise PackLoadError(msg)

        # The directory name is authoritative. A manifest whose key disagrees
        # would load fine and then fail confusingly at lookup time.
        declared = raw.get("key")
        if declared is not None and str(declared) != key:
            msg = (
                f"pack in directory {key!r} declares key {declared!r}; "
                "the directory name and the manifest key must match"
            )
            raise PackLoadError(msg)
        raw["key"] = key

        try:
            return DomainPack(**raw)
        except ValidationError as exc:
            msg = f"pack {key!r} at {manifest} is invalid: {exc}"
            raise PackLoadError(msg) from exc
