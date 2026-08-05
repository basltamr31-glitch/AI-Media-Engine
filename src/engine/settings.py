"""Application configuration.

Settings are read from environment variables (prefix ``ENGINE_``) and an optional
``.env`` file. Nothing in :mod:`engine.core` may import this module -- the domain
must stay independent of deployment concerns.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "staging", "production"]
LogFormat = Literal["console", "json"]


class Settings(BaseSettings):
    """Runtime configuration for every entry point (CLI today, API later)."""

    model_config = SettingsConfigDict(
        env_prefix="ENGINE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # --- Runtime ---------------------------------------------------------
    environment: Environment = "local"
    log_level: str = "INFO"
    log_format: LogFormat = "console"

    # --- Storage ---------------------------------------------------------
    # SQLite locally; the schema is intentionally restricted to a
    # Postgres-compatible subset so this is the only line that changes on the
    # way to a hosted database.
    database_url: str = "sqlite+pysqlite:///./data/engine.db"
    database_echo: bool = False

    # --- Paths -----------------------------------------------------------
    config_dir: Path = Path("config")
    data_dir: Path = Path("data")
    output_dir: Path = Path("output")

    # --- Tenancy ---------------------------------------------------------
    # Single-tenant in practice, multi-tenant in shape. Every query is already
    # scoped by workspace; this just selects which one the CLI acts on.
    default_workspace_slug: str = "default"

    # --- LLM (Phase 3) ---------------------------------------------------
    anthropic_api_key: SecretStr | None = None
    generation_model: str = "claude-opus-5"
    extraction_model: str = "claude-haiku-4-5"

    # --- Cost guardrails -------------------------------------------------
    # Unit economics are a first-class concern, so the ceiling exists before
    # the first paid call is ever made.
    max_cost_usd_per_run: float = Field(default=5.0, gt=0)

    @field_validator("log_level")
    @classmethod
    def _normalise_log_level(cls, value: str) -> str:
        level = value.upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if level not in allowed:
            msg = f"log_level must be one of {sorted(allowed)}, got {value!r}"
            raise ValueError(msg)
        return level

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def ensure_directories(self) -> None:
        """Create the writable directories the engine expects.

        Called by entry points rather than at import time -- importing a module
        should never have filesystem side effects.
        """
        for directory in (self.data_dir, self.output_dir):
            directory.mkdir(parents=True, exist_ok=True)


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so configuration is read once and stays stable for the process.
    Tests clear the cache via ``get_settings.cache_clear()``.
    """
    return Settings()
