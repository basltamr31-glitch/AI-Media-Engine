"""Tests for the domain pack model and loader (ADR 0014)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from engine.adapters.packs.loader import FilesystemPackLoader
from engine.core.domain.pack import DomainPack, TaxonomyNode
from engine.core.ports.packs import PackLoadError, PackNotFoundError

MANIFEST = """
key: widgets
version: 2
name: Widget Industry
description: A synthetic pack unrelated to any real domain.
taxonomy:
  - {key: launch, label: Launch}
  - {key: recall, label: Recall}
  - {key: firmware, label: Firmware, parent: recall}
"""


def write_pack(root, key: str, body: str) -> None:
    directory = root / key
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pack.yaml").write_text(body, encoding="utf-8")


class TestDomainPack:
    def test_identifier_combines_key_and_version(self):
        pack = DomainPack(key="widgets", version=3, name="W")
        assert pack.identifier == "widgets@3"

    @pytest.mark.parametrize("key", ["engine", "core", "default"])
    def test_reserved_keys_are_rejected(self, key: str):
        with pytest.raises(ValidationError, match="reserved"):
            DomainPack(key=key, version=1, name="X")

    @pytest.mark.parametrize("key", ["", "has space", "punctuation!"])
    def test_malformed_keys_are_rejected(self, key: str):
        with pytest.raises(ValidationError):
            DomainPack(key=key, version=1, name="X")

    def test_version_must_be_positive(self):
        with pytest.raises(ValidationError):
            DomainPack(key="widgets", version=0, name="X")

    def test_duplicate_taxonomy_keys_are_rejected(self):
        with pytest.raises(ValidationError, match="duplicate taxonomy keys"):
            DomainPack(
                key="widgets",
                version=1,
                name="X",
                taxonomy=[
                    TaxonomyNode(key="a", label="A"),
                    TaxonomyNode(key="a", label="Also A"),
                ],
            )

    def test_unknown_parent_is_rejected(self):
        with pytest.raises(ValidationError, match="unknown parent"):
            DomainPack(
                key="widgets",
                version=1,
                name="X",
                taxonomy=[TaxonomyNode(key="child", label="C", parent="ghost")],
            )

    def test_excessive_nesting_is_rejected(self):
        """A taxonomy deep enough to need four levels has become a tagging system."""
        nodes = [TaxonomyNode(key="l1", label="1")]
        for depth in range(2, 6):
            nodes.append(TaxonomyNode(key=f"l{depth}", label=str(depth), parent=f"l{depth - 1}"))
        with pytest.raises(ValidationError, match="levels deep"):
            DomainPack(key="widgets", version=1, name="X", taxonomy=nodes)


class TestTaxonomyNavigation:
    @pytest.fixture
    def pack(self) -> DomainPack:
        return DomainPack(
            key="widgets",
            version=1,
            name="X",
            taxonomy=[
                TaxonomyNode(key="launch", label="Launch"),
                TaxonomyNode(key="recall", label="Recall"),
                TaxonomyNode(key="firmware", label="Firmware", parent="recall"),
            ],
        )

    def test_roots_exclude_children(self, pack: DomainPack):
        assert {node.key for node in pack.roots()} == {"launch", "recall"}

    def test_children_of(self, pack: DomainPack):
        assert [node.key for node in pack.children_of("recall")] == ["firmware"]

    def test_node_lookup(self, pack: DomainPack):
        assert pack.node("firmware") is not None
        assert pack.node("missing") is None

    def test_taxonomy_keys(self, pack: DomainPack):
        assert pack.taxonomy_keys == {"launch", "recall", "firmware"}


class TestFilesystemPackLoader:
    def test_loads_a_valid_pack(self, tmp_path):
        write_pack(tmp_path, "widgets", MANIFEST)
        pack = FilesystemPackLoader(tmp_path).load("widgets")
        assert pack.identifier == "widgets@2"
        assert len(pack.taxonomy) == 3

    def test_lists_available_packs(self, tmp_path):
        write_pack(tmp_path, "widgets", MANIFEST)
        write_pack(tmp_path, "gadgets", MANIFEST.replace("key: widgets", "key: gadgets"))
        assert FilesystemPackLoader(tmp_path).list_keys() == ["gadgets", "widgets"]

    def test_ignores_directories_without_a_manifest(self, tmp_path):
        (tmp_path / "not-a-pack").mkdir()
        assert FilesystemPackLoader(tmp_path).list_keys() == []

    def test_missing_pack_lists_what_is_available(self, tmp_path):
        write_pack(tmp_path, "widgets", MANIFEST)
        with pytest.raises(PackNotFoundError, match="available: widgets"):
            FilesystemPackLoader(tmp_path).load("ghost")

    def test_missing_root_directory_is_not_a_crash(self, tmp_path):
        assert FilesystemPackLoader(tmp_path / "nope").list_keys() == []

    def test_invalid_yaml_is_reported_as_a_load_error(self, tmp_path):
        write_pack(tmp_path, "broken", "key: [unclosed\n")
        with pytest.raises(PackLoadError, match="invalid YAML"):
            FilesystemPackLoader(tmp_path).load("broken")

    def test_non_mapping_manifest_is_rejected(self, tmp_path):
        write_pack(tmp_path, "broken", "- just\n- a\n- list\n")
        with pytest.raises(PackLoadError, match="must be a mapping"):
            FilesystemPackLoader(tmp_path).load("broken")

    def test_schema_violations_are_reported_with_the_path(self, tmp_path):
        write_pack(tmp_path, "broken", "version: 0\nname: X\n")
        with pytest.raises(PackLoadError, match="is invalid"):
            FilesystemPackLoader(tmp_path).load("broken")

    def test_directory_name_wins_over_a_disagreeing_manifest(self, tmp_path):
        """A mismatch would load fine and then fail confusingly at lookup."""
        write_pack(tmp_path, "widgets", MANIFEST.replace("key: widgets", "key: something-else"))
        with pytest.raises(PackLoadError, match="must match"):
            FilesystemPackLoader(tmp_path).load("widgets")

    def test_manifest_without_a_key_inherits_the_directory_name(self, tmp_path):
        write_pack(tmp_path, "widgets", MANIFEST.replace("key: widgets\n", ""))
        assert FilesystemPackLoader(tmp_path).load("widgets").key == "widgets"

    def test_packs_are_cached_after_first_load(self, tmp_path):
        write_pack(tmp_path, "widgets", MANIFEST)
        loader = FilesystemPackLoader(tmp_path)
        first = loader.load("widgets")
        (tmp_path / "widgets" / "pack.yaml").unlink()
        assert loader.load("widgets") is first

    def test_cache_can_be_cleared(self, tmp_path):
        write_pack(tmp_path, "widgets", MANIFEST)
        loader = FilesystemPackLoader(tmp_path)
        loader.load("widgets")
        loader.clear_cache()
        (tmp_path / "widgets" / "pack.yaml").unlink()
        with pytest.raises(PackNotFoundError):
            loader.load("widgets")


class TestShippedPack:
    """The ai-news pack must always be valid -- it is the reference example."""

    def test_ai_news_pack_loads(self):
        from engine.cli.container import find_project_root

        pack = FilesystemPackLoader(find_project_root() / "packs").load("ai-news")
        assert pack.key == "ai-news"
        assert pack.taxonomy_keys >= {"model_release", "research", "policy"}
        benchmarks = pack.node("benchmarks")
        assert benchmarks is not None
        assert benchmarks.parent == "research"
