"""Domain packs -- the subject-matter knowledge the engine deliberately lacks.

ADR 0014. The engine knows how to fetch, deduplicate, rank, write and publish.
It knows nothing about AI, finance or medicine. A pack supplies that, as data.

``packs/ai-news/`` is the first pack, which makes AI news the first customer of
the platform rather than the shape of it.

Sections are added as the milestones that consume them land: taxonomy now
(Phase 2 classifies against it), channels in 1.5.5, ranking signals in 1.5.6.
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import Field, field_validator, model_validator

from engine.core.domain.base import DomainModel

#: Reserved for engine-level defaults that no pack may claim.
RESERVED_PACK_KEYS: Final = frozenset({"engine", "core", "default"})

_MAX_TAXONOMY_DEPTH: Final = 3


class TaxonomyNode(DomainModel):
    """One classification category.

    Flat storage with parent references rather than nesting: a flat list is
    easier to write by hand, diff in review, and query, and depth is validated
    on load instead of being implied by indentation.
    """

    key: str = Field(description="Stable identifier, unique within the pack.")
    label: str = Field(description="Human-readable name.")
    parent: str | None = Field(
        default=None,
        description="Key of the parent node. None for a root category.",
    )
    description: str | None = Field(
        default=None,
        description="What belongs here. Doubles as classifier guidance.",
    )

    @field_validator("key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        key = value.strip().lower()
        if not key or not key.replace("_", "").replace("-", "").isalnum():
            msg = f"taxonomy key must be alphanumeric with - or _; got {value!r}"
            raise ValueError(msg)
        return key


class DomainPack(DomainModel):
    """A versioned bundle of domain knowledge."""

    key: str = Field(description="Stable pack identifier, e.g. 'ai-news'.")
    version: int = Field(ge=1, description="Bumped on any content change.")
    name: str
    description: str = ""
    taxonomy: list[TaxonomyNode] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        key = value.strip().lower()
        if key in RESERVED_PACK_KEYS:
            msg = f"pack key {key!r} is reserved for the engine"
            raise ValueError(msg)
        if not key or not key.replace("-", "").replace("_", "").isalnum():
            msg = f"pack key must be alphanumeric with - or _; got {value!r}"
            raise ValueError(msg)
        return key

    @model_validator(mode="after")
    def _validate_taxonomy(self) -> DomainPack:
        keys = [node.key for node in self.taxonomy]
        duplicates = {key for key in keys if keys.count(key) > 1}
        if duplicates:
            msg = f"duplicate taxonomy keys in pack {self.key!r}: {sorted(duplicates)}"
            raise ValueError(msg)

        known = set(keys)
        for node in self.taxonomy:
            if node.parent is not None and node.parent not in known:
                msg = (
                    f"taxonomy node {node.key!r} references unknown parent "
                    f"{node.parent!r} in pack {self.key!r}"
                )
                raise ValueError(msg)

        # A cycle would make depth calculation loop forever, and a deep tree is
        # a sign the taxonomy is being used as a tagging system.
        for node in self.taxonomy:
            depth = self._depth_of(node)
            if depth > _MAX_TAXONOMY_DEPTH:
                msg = (
                    f"taxonomy node {node.key!r} is nested {depth} levels deep; "
                    f"the maximum is {_MAX_TAXONOMY_DEPTH}"
                )
                raise ValueError(msg)
        return self

    def _depth_of(self, node: TaxonomyNode) -> int:
        by_key = {candidate.key: candidate for candidate in self.taxonomy}
        depth = 1
        seen = {node.key}
        current = node
        while current.parent is not None:
            if current.parent in seen:
                msg = f"taxonomy cycle through {current.parent!r} in pack {self.key!r}"
                raise ValueError(msg)
            seen.add(current.parent)
            current = by_key[current.parent]
            depth += 1
            if depth > _MAX_TAXONOMY_DEPTH + 1:
                break
        return depth

    @property
    def taxonomy_keys(self) -> set[str]:
        return {node.key for node in self.taxonomy}

    def node(self, key: str) -> TaxonomyNode | None:
        return next((node for node in self.taxonomy if node.key == key), None)

    def roots(self) -> list[TaxonomyNode]:
        return [node for node in self.taxonomy if node.parent is None]

    def children_of(self, key: str) -> list[TaxonomyNode]:
        return [node for node in self.taxonomy if node.parent == key]

    @property
    def identifier(self) -> str:
        """``key@version``, used in logs and to stamp what was active."""
        return f"{self.key}@{self.version}"
