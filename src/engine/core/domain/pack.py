"""Domain packs -- the subject-matter knowledge the engine deliberately lacks.

ADR 0014. The engine knows how to fetch, deduplicate, rank, write and publish.
It knows nothing about AI, finance or medicine. A pack supplies that, as data.

``packs/ai-news/`` is the first pack, which makes AI news the first customer of
the platform rather than the shape of it.

Sections are added as the milestones that consume them land: taxonomy now
(Phase 2 classifies against it), channels in 1.5.5, ranking signals in 1.5.6.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Final

from pydantic import Field, field_validator, model_validator

from engine.core.domain.base import DomainModel
from engine.core.domain.policy import WEIGHT_SUM_TOLERANCE, ScoringProfile

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


class ProductKind(StrEnum):
    """How the engine treats an output product.

    Engine-controlled and genuinely closed: the kind selects *behaviour* --
    whether a product is published on its own, attached to something else, or
    handed to another tool. The specific product is pack data; the class of
    thing is mechanics. Same split as ``SourceKind`` versus ``parser_key``.
    """

    POST = "post"  # publishable on its own
    ATTACHMENT = "attachment"  # accompanies a post: caption, hashtags
    SCRIPT = "script"  # spoken or performed text
    PROMPT = "prompt"  # input to another tool, e.g. image generation


class ProductDef(DomainModel):
    """One kind of output the pack knows how to produce.

    Replaces the ``ContentKind`` enum (ADR 0015), which was shaped like social
    media and so could not express a research digest without a code change.
    """

    key: str = Field(description="Stable identifier, unique within the pack.")
    name: str
    kind: ProductKind
    description: str | None = None

    @field_validator("key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        return _validate_vocabulary_key(value, "product")


class ChannelDef(DomainModel):
    """One distribution channel and its hard formatting constraints.

    Replaces the ``Channel`` enum and the separate ``channel_specs/`` files.
    Adding Threads, Bluesky or a newsletter is now a pack edit rather than a
    code change plus a migration -- which is how sources have always worked.
    """

    key: str = Field(description="Stable identifier, unique within the pack.")
    name: str
    max_characters: int = Field(gt=0)
    supports_hashtags: bool = True
    max_hashtags: int = Field(default=0, ge=0)
    supports_links: bool = True
    preferred_products: list[str] = Field(
        default_factory=list,
        description="Product keys this channel prefers, most important first.",
    )
    format_notes: list[str] = Field(
        default_factory=list,
        description="Channel conventions passed to generation as explicit rules.",
    )

    @field_validator("key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        return _validate_vocabulary_key(value, "channel")

    @model_validator(mode="after")
    def _validate_hashtags(self) -> ChannelDef:
        if self.supports_hashtags and self.max_hashtags == 0:
            msg = f"channel {self.key!r} supports hashtags but max_hashtags is 0"
            raise ValueError(msg)
        return self


class Normaliser(StrEnum):
    """How a raw signal value is mapped onto 0..1.

    Engine-controlled and closed: each member selects an implementation, so a
    new one implies new engine code anyway. Which normaliser a signal *uses* is
    pack data; the set of available normalisers is mechanics.
    """

    PASSTHROUGH = "passthrough"  # already 0..1; clamped
    LINEAR = "linear"  # value / ceiling, clamped
    LOGARITHMIC = "logarithmic"  # log(1+v) / log(1+ceiling); for attention counts
    DECAY = "decay"  # exponential decay by half-life; for recency


class RankingSignal(DomainModel):
    """One input to the editorial score.

    Named ``RankingSignal`` rather than ``SignalDef`` deliberately: the domain
    already has :class:`engine.core.domain.signal.Signal`, which is a *feedback
    event*. These are ranking inputs. One word, two concepts, would be a
    reliable source of confusion.
    """

    key: str = Field(description="Stable identifier, unique within the pack.")
    name: str
    normaliser: Normaliser = Normaliser.PASSTHROUGH
    default_weight: float = Field(
        ge=0.0,
        le=1.0,
        description="This signal's share of the pack's default scoring profile.",
    )
    parameters: dict[str, float] = Field(
        default_factory=dict,
        description="Normaliser inputs, e.g. 'ceiling' or 'half_life_hours'.",
    )
    description: str | None = None

    @field_validator("key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        return _validate_vocabulary_key(value, "ranking signal")


def _validate_vocabulary_key(value: str, label: str) -> str:
    key = value.strip().lower()
    if not key or not key.replace("_", "").replace("-", "").isalnum():
        msg = f"{label} key must be alphanumeric with - or _; got {value!r}"
        raise ValueError(msg)
    return key


class DomainPack(DomainModel):
    """A versioned bundle of domain knowledge."""

    key: str = Field(description="Stable pack identifier, e.g. 'ai-news'.")
    version: int = Field(ge=1, description="Bumped on any content change.")
    name: str
    description: str = ""
    taxonomy: list[TaxonomyNode] = Field(default_factory=list)
    products: list[ProductDef] = Field(default_factory=list)
    channels: list[ChannelDef] = Field(default_factory=list)
    ranking_signals: list[RankingSignal] = Field(default_factory=list)
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

    @model_validator(mode="after")
    def _validate_vocabularies(self) -> DomainPack:
        for label, keys in (
            ("product", [product.key for product in self.products]),
            ("channel", [channel.key for channel in self.channels]),
            ("ranking signal", [signal.key for signal in self.ranking_signals]),
        ):
            duplicates = {key for key in keys if keys.count(key) > 1}
            if duplicates:
                msg = f"duplicate {label} keys in pack {self.key!r}: {sorted(duplicates)}"
                raise ValueError(msg)

        # A channel preferring a product that does not exist would fail at
        # generation time, in Phase 3, far from the typo that caused it.
        known_products = {product.key for product in self.products}
        for channel in self.channels:
            unknown = [key for key in channel.preferred_products if key not in known_products]
            if unknown:
                msg = (
                    f"channel {channel.key!r} prefers unknown product(s) "
                    f"{sorted(unknown)} in pack {self.key!r}"
                )
                raise ValueError(msg)

        # The pack's own defaults must be a usable profile. Catching this here
        # means a pack ships either ranking-ready or not at all.
        if self.ranking_signals:
            total = sum(signal.default_weight for signal in self.ranking_signals)
            if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
                msg = (
                    f"default ranking weights in pack {self.key!r} must sum to 1.0, "
                    f"got {total:.6f} across {len(self.ranking_signals)} signal(s)"
                )
                raise ValueError(msg)
        return self

    @property
    def taxonomy_keys(self) -> set[str]:
        return {node.key for node in self.taxonomy}

    @property
    def product_keys(self) -> set[str]:
        return {product.key for product in self.products}

    @property
    def channel_keys(self) -> set[str]:
        return {channel.key for channel in self.channels}

    @property
    def ranking_signal_keys(self) -> set[str]:
        return {signal.key for signal in self.ranking_signals}

    @property
    def default_scoring_profile(self) -> ScoringProfile:
        """The profile a workspace inherits when it declares no weights."""
        return ScoringProfile(
            weights={signal.key: signal.default_weight for signal in self.ranking_signals}
        )

    def ranking_signal(self, key: str) -> RankingSignal | None:
        return next((signal for signal in self.ranking_signals if signal.key == key), None)

    def product(self, key: str) -> ProductDef | None:
        return next((product for product in self.products if product.key == key), None)

    def channel(self, key: str) -> ChannelDef | None:
        return next((channel for channel in self.channels if channel.key == key), None)

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
