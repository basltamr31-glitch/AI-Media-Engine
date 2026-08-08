"""Policy-as-data loading.

These tests run against the real files in ``config/``, so a broken policy file
fails CI rather than a 3am pipeline run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.adapters.config.policy_loader import PolicyLoader, PolicyLoadError
from engine.core.domain.enums import SourceKind
from engine.core.domain.language import ARABIC

pytestmark = pytest.mark.integration


@pytest.fixture
def loader() -> PolicyLoader:
    return PolicyLoader(Path("config"))


class TestShippedConfiguration:
    def test_default_workspace_is_valid(self, loader: PolicyLoader):
        config = loader.load_workspace("default")
        assert config.slug == "default"
        assert ARABIC in config.languages

    def test_ships_with_multiple_source_transports(self, loader: PolicyLoader):
        """Single-source dependency is a strategic failure mode, not just a technical one."""
        config = loader.load_workspace("default")
        kinds = {source.kind for source in config.sources}
        assert len(config.sources) >= 3
        assert kinds >= {SourceKind.EMAIL, SourceKind.RSS, SourceKind.API}

    def test_primary_source_requirement_is_enabled(self, loader: PolicyLoader):
        """Guards the 'newsletters are tips, not text' rule against silent regression."""
        assert loader.load_workspace("default").editorial.require_primary_source

    def test_every_referenced_brand_profile_exists(self, loader: PolicyLoader):
        config = loader.load_workspace("default")
        assert config.brand_profiles
        for key in config.brand_profiles:
            profile = loader.load_brand_profile(key, workspace_id="ws_test")
            assert profile.key == key
            assert profile.workspace_id == "ws_test"

    def test_arabic_profile_is_configured(self, loader: PolicyLoader):
        profile = loader.load_brand_profile("default_ar", workspace_id="ws_test")
        assert profile.language == ARABIC
        assert profile.tone_rules


class TestErrorHandling:
    def test_missing_file_raises(self, loader: PolicyLoader):
        with pytest.raises(PolicyLoadError, match="not found"):
            loader.load_workspace("does-not-exist")

    def test_invalid_yaml_raises(self, tmp_path: Path):
        workspaces = tmp_path / "workspaces"
        workspaces.mkdir()
        (workspaces / "broken.yaml").write_text("slug: [unclosed", encoding="utf-8")
        with pytest.raises(PolicyLoadError, match="invalid YAML"):
            PolicyLoader(tmp_path).load_workspace("broken")

    def test_schema_violation_raises(self, tmp_path: Path):
        workspaces = tmp_path / "workspaces"
        workspaces.mkdir()
        (workspaces / "bad.yaml").write_text(
            "slug: ok-slug\nname: Test\neditorial:\n  weights:\n"
            "    corroboration: 0.9\n    source_authority: 0.9\n"
            "    recency: 0.1\n    prominence: 0.1\n    audience_fit: 0.1\n",
            encoding="utf-8",
        )
        with pytest.raises(PolicyLoadError, match="invalid workspace config"):
            PolicyLoader(tmp_path).load_workspace("bad")
