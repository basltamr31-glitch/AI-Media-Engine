"""Settings behaviour."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from engine.settings import Settings


def test_defaults_are_usable_without_any_environment():
    """Phase 0 must run with zero configuration -- no accounts, no secrets."""
    settings = Settings(_env_file=None)
    assert settings.is_sqlite
    assert settings.anthropic_api_key is None
    assert settings.max_cost_usd_per_run > 0


def test_log_level_is_normalised():
    assert Settings(_env_file=None, log_level="debug").log_level == "DEBUG"


def test_invalid_log_level_is_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, log_level="chatty")


def test_environment_variables_are_read(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENGINE_ENVIRONMENT", "production")
    monkeypatch.setenv("ENGINE_DEFAULT_WORKSPACE_SLUG", "acme")
    settings = Settings(_env_file=None)
    assert settings.environment == "production"
    assert settings.default_workspace_slug == "acme"


def test_postgres_url_is_not_flagged_as_sqlite():
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@host/db")
    assert not settings.is_sqlite


def test_api_key_is_not_exposed_by_repr(monkeypatch: pytest.MonkeyPatch):
    """Secrets must never leak into logs or crash reports."""
    monkeypatch.setenv("ENGINE_ANTHROPIC_API_KEY", "sk-ant-secret-value")
    settings = Settings(_env_file=None)
    assert "sk-ant-secret-value" not in repr(settings)
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-secret-value"


def test_settings_are_immutable():
    settings = Settings(_env_file=None)
    with pytest.raises(ValidationError):
        # Also a static error; asserted at runtime so config cannot drift
        # mid-process even from untyped call sites.
        settings.environment = "production"  # type: ignore[misc]
