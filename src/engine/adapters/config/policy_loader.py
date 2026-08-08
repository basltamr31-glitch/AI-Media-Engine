"""Loading policy-as-data from YAML.

Validation happens at load time, so a malformed brand profile fails at startup
with a precise error rather than producing subtly wrong content at 3am.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from engine.adapters.config.schema import WorkspaceConfig
from engine.core.domain.content import BrandProfile


class PolicyLoadError(RuntimeError):
    """Raised when a policy file is missing, unreadable or invalid."""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        msg = f"policy file not found: {path}"
        raise PolicyLoadError(msg)
    try:
        # safe_load only: policy files are data, never executable Python objects.
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"invalid YAML in {path}: {exc}"
        raise PolicyLoadError(msg) from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        msg = f"expected a mapping at the top level of {path}, got {type(loaded).__name__}"
        raise PolicyLoadError(msg)
    return loaded


class PolicyLoader:
    """Reads workspace and brand policy from the config directory.

    Channel definitions moved into the domain pack in milestone 1.5.5 (ADR 0015):
    they are facts about platforms, not tenant configuration.
    """

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir

    @property
    def workspaces_dir(self) -> Path:
        return self._config_dir / "workspaces"

    @property
    def brand_profiles_dir(self) -> Path:
        return self._config_dir / "brand_profiles"

    def load_workspace(self, slug: str) -> WorkspaceConfig:
        """Load and validate one workspace definition."""
        payload = _read_yaml(self.workspaces_dir / f"{slug}.yaml")
        try:
            return WorkspaceConfig.model_validate(payload)
        except ValueError as exc:
            msg = f"invalid workspace config {slug!r}: {exc}"
            raise PolicyLoadError(msg) from exc

    def list_workspace_slugs(self) -> list[str]:
        if not self.workspaces_dir.is_dir():
            return []
        return sorted(path.stem for path in self.workspaces_dir.glob("*.yaml"))

    def load_brand_profile(self, key: str, workspace_id: str) -> BrandProfile:
        """Load a brand voice definition and bind it to a workspace."""
        payload = _read_yaml(self.brand_profiles_dir / f"{key}.yaml")
        payload.setdefault("key", key)
        payload["workspace_id"] = workspace_id
        try:
            return BrandProfile.model_validate(payload)
        except ValueError as exc:
            msg = f"invalid brand profile {key!r}: {exc}"
            raise PolicyLoadError(msg) from exc
