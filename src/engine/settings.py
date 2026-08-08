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
LlmProvider = Literal["ollama", "anthropic"]


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

    #: How many stored documents one parse run considers. Higher than the
    #: ingest ceiling because parsing is local work over an existing backlog,
    #: not network calls against someone else's rate limit.
    parse_max_documents: int = Field(default=200, gt=0, le=5000)

    #: How many unresolved candidates one resolve run considers.
    resolve_max_candidates: int = Field(default=500, gt=0, le=10000)
    #: Days of existing stories new mentions are matched against. A story
    #: covered on Monday and again on Thursday is one story.
    resolve_window_days: int = Field(default=7, gt=0, le=90)
    #: Headline overlap required to merge two mentions with different links.
    #: Set above every pair observed in the calibration corpus: a wrong merge
    #: cannot be undone downstream, a missed one costs a duplicate.
    resolve_similarity_threshold: float = Field(default=0.9, ge=0.0, le=1.0)

    #: Primary sources fetched per enrich run. Deliberately small: each one
    #: is a request to somebody else's server, made one at a time.
    enrich_max_stories: int = Field(default=25, gt=0, le=500)
    #: Attempts before a URL is presumed permanently unreachable. Most
    #: enrichment failures -- paywalls, dead links -- never recover.
    enrich_max_attempts: int = Field(default=3, gt=0, le=10)
    #: Characters below which an extraction is a stub rather than an article.
    enrich_min_text_length: int = Field(default=400, gt=0, le=10000)

    #: Stories whose provenance is reconciled per run. Cheap -- no network,
    #: no model -- so this is generous where the enrich ceiling is not.
    evidence_max_stories: int = Field(default=200, gt=0, le=5000)

    #: Stories scored per run. Pure arithmetic over loaded rows, so this is
    #: bounded by memory rather than by anyone else's rate limit.
    score_max_stories: int = Field(default=200, gt=0, le=5000)

    #: The period the selection quota is counted over. A day, matching the
    #: publishing rhythm: two runs in one morning top up to the day's limit
    #: rather than producing two batches.
    select_window_hours: int = Field(default=24, gt=0, le=720)

    #: Selected stories written for in one run. Small because each story
    #: multiplies out by channel and language: three stories across three
    #: channels and two languages is already eighteen model calls.
    generate_max_stories: int = Field(default=5, gt=0, le=100)
    #: Output ceiling per post. Sized for Arabic rather than English: the same
    #: post costs roughly twice as many tokens in Arabic, and at 2048 a
    #: full-length Arabic LinkedIn post was observed running out of budget
    #: mid-sentence while its English twin finished comfortably.
    generate_max_tokens: int = Field(default=4096, ge=256, le=32_000)

    http_timeout_seconds: float = Field(default=30.0, gt=0)
    #: Identifying ourselves honestly is both etiquette and self-interest: an
    #: anonymous scraper is the first thing a publisher blocks.
    http_user_agent: str = "ai-media-engine/0.1 (+https://github.com/; contact via repo)"

    # --- Politeness (Phase 6) --------------------------------------------
    #: Whether to obey ``robots.txt``. On by default and only switchable for
    #: fetching one's own sites, where the rules are the operator's own to set.
    #: Turning it off to reach a third party's pages is a decision this project
    #: does not endorse, which is why it is a setting rather than a flag on the
    #: command that would make it convenient.
    http_respect_robots: bool = True
    #: Minimum gap between two requests to the same host, unless the site's
    #: ``Crawl-delay`` asks for more.
    http_min_interval_seconds: float = Field(default=1.0, ge=0.0, le=60.0)
    #: How long a fetched ``robots.txt`` is trusted before being re-read.
    http_robots_ttl_seconds: float = Field(default=86_400.0, gt=0)

    # --- LLM (Phase 3) ---------------------------------------------------
    #: Which implementation of the LLM port the container builds. Local by
    #: default: generation quality has to be judged before it is paid for, and
    #: a per-token bill turns every prompt iteration into a purchase decision.
    llm_provider: LlmProvider = "ollama"

    #: Local inference. Costs nothing, runs offline, and keeps scraped article
    #: text on this machine rather than in a third party's training corpus.
    ollama_base_url: str = "http://localhost:11434"
    #: Small enough to run on a laptop with a browser and an IDE open, and
    #: verified to extract accurately from real article text. ``qwen3:4b`` is
    #: the better model where ~3.5 GB of RAM is free -- see docs/LLM.md.
    ollama_model: str = "qwen3:1.7b"
    #: Minutes, not seconds: a CPU-bound generation is slow but not broken.
    ollama_timeout_seconds: float = Field(default=600.0, gt=0)
    #: Ollama's own default silently truncates a long article, which is how a
    #: system starts writing confidently about text it never read.
    ollama_context_tokens: int = Field(default=8192, ge=2048, le=131_072)

    #: Hosted provider, enabled by setting ``llm_provider`` once local output
    #: is good enough to be worth paying for.
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
    def active_model(self) -> str:
        """The model name to send to whichever provider is configured.

        Model names are provider-specific, so the *setting* that supplies them
        has to follow the provider. Resolved here rather than at each call site
        so no caller can ask Ollama for ``claude-opus-5``.
        """
        return self.ollama_model if self.llm_provider == "ollama" else self.generation_model

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
