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
    packs_dir: Path = Path("packs")
    data_dir: Path = Path("data")
    output_dir: Path = Path("output")

    # --- Tenancy ---------------------------------------------------------
    # Single-tenant in practice, multi-tenant in shape. Every query is already
    # scoped by workspace; this just selects which one the CLI acts on.
    default_workspace_slug: str = "default"
    #: Account that owns workspaces which do not name one (ADR 0011).
    default_account_slug: str = "default"

    # --- Ingestion (Phase 1) ---------------------------------------------
    #: OAuth *client* identity, downloaded from Google Cloud. Identifies the
    #: application, not the user. Created manually, never by the code.
    google_credentials_file: Path = Path("credentials.json")
    #: Cached user tokens, written by `engine sources auth`. Delete to re-consent.
    google_token_file: Path = Path("token.json")

    #: Per-source ceiling on documents fetched in one run. A safety valve
    #: against a misconfigured query pulling an entire inbox.
    ingest_max_documents: int = Field(default=50, gt=0, le=500)
    #: How far back to look on each run. Generous enough to survive a few days
    #: of downtime without manual intervention.
    ingest_lookback_days: int = Field(default=7, gt=0, le=365)
    #: Archiving raw payloads costs disk and buys replay, fixtures and provenance.
    ingest_archive_raw: bool = True

    http_timeout_seconds: float = Field(default=30.0, gt=0)
    #: Identifying ourselves honestly is both etiquette and self-interest: an
    #: anonymous scraper is the first thing a publisher blocks.
    http_user_agent: str = "ai-media-engine/0.1 (+https://github.com/; contact via repo)"

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

    @property
    def raw_archive_dir(self) -> Path:
        """Where verbatim fetched payloads are kept.

        Under ``data/`` rather than ``output/``: this is machine-local input we
        may need to replay, not a deliverable.
        """
        return self.data_dir / "raw"

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
