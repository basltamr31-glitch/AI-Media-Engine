"""Composition root.

The one place where concrete implementations are chosen and wired together.
Everything below this module receives its dependencies; nothing below it calls
a constructor for infrastructure.

Keeping composition in a single, thin layer is what makes the eventual HTTP API
a second entry point rather than a second application -- it will build the same
container and reuse every stage unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from engine.adapters.clock import SystemClock
from engine.adapters.config.policy_loader import PolicyLoader
from engine.adapters.http import HttpxClient
from engine.adapters.persistence.session import create_db_engine, create_session_factory
from engine.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWork
from engine.adapters.sources.archive import FileSystemRawArchive, NullRawArchive
from engine.adapters.sources.email.gmail import GmailApiClient, GmailFetcher, GoogleApiGmailClient
from engine.adapters.sources.email.oauth import GoogleCredentialStore
from engine.adapters.sources.json_api import JsonApiFetcher
from engine.adapters.sources.registry import FetcherRegistry, ParserRegistry
from engine.adapters.sources.rss import RssFetcher
from engine.core.ports.clock import Clock
from engine.core.ports.http import HttpClient
from engine.core.ports.sources import RawDocumentArchive
from engine.observability.logging import configure_logging
from engine.settings import Settings, get_settings


def find_project_root(start: Path | None = None) -> Path:
    """Locate the project root by walking up to the directory holding pyproject.toml.

    Commands must behave identically whether invoked from the repository root or
    a subdirectory.
    """
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return current


# Note: no `slots=True`. `functools.cached_property` stores its cache in the
# instance `__dict__`, which slotted classes do not have.
@dataclass
class Container:
    """Application dependencies, constructed lazily and shared per process."""

    settings: Settings
    project_root: Path

    def resolve_path(self, path: Path) -> Path:
        """Anchor a relative configured path to the project root.

        Commands must behave the same whether invoked from the repository root
        or a subdirectory, so relative paths never depend on the shell's cwd.
        """
        return path if path.is_absolute() else self.project_root / path

    @cached_property
    def clock(self) -> Clock:
        return SystemClock()

    @cached_property
    def db_engine(self) -> Engine:
        return create_db_engine(self.settings)

    @cached_property
    def session_factory(self) -> sessionmaker[Session]:
        return create_session_factory(self.db_engine)

    @cached_property
    def policy_loader(self) -> PolicyLoader:
        return PolicyLoader(self.resolve_path(self.settings.config_dir))

    @cached_property
    def http(self) -> HttpClient:
        return HttpxClient(
            timeout_seconds=self.settings.http_timeout_seconds,
            user_agent=self.settings.http_user_agent,
        )

    @cached_property
    def credential_store(self) -> GoogleCredentialStore:
        return GoogleCredentialStore(
            credentials_file=self.resolve_path(self.settings.google_credentials_file),
            token_file=self.resolve_path(self.settings.google_token_file),
        )

    def gmail_client(self) -> GmailApiClient:
        """Build an authenticated Gmail client.

        Deliberately a method, not a cached property: it is called lazily by the
        fetcher only when an email source is actually processed. A workspace
        with only RSS sources must run on a machine that has never authenticated
        with Google.
        """
        return GoogleApiGmailClient(self.credential_store.load())

    @cached_property
    def raw_archive(self) -> RawDocumentArchive:
        if not self.settings.ingest_archive_raw:
            return NullRawArchive()
        return FileSystemRawArchive(self.resolve_path(self.settings.raw_archive_dir))

    @cached_property
    def parsers(self) -> ParserRegistry:
        # Populated in Phase 2, when newsletter parsing arrives.
        return ParserRegistry()

    @cached_property
    def fetchers(self) -> FetcherRegistry:
        """One fetcher per transport.

        Adding a *source* never touches this method -- only adding a genuinely
        new transport does. Reddit, Hacker News and GitHub Releases are all
        served by the existing JSON fetcher through configuration alone.
        """
        registry = FetcherRegistry()
        registry.register(
            GmailFetcher(
                client_factory=self.gmail_client,
                clock=self.clock,
                lookback_days=self.settings.ingest_lookback_days,
            )
        )
        registry.register(RssFetcher(http=self.http, clock=self.clock))
        registry.register(JsonApiFetcher(http=self.http, clock=self.clock))
        return registry

    def unit_of_work(self) -> SqlAlchemyUnitOfWork:
        """Create a fresh transactional scope. Never share one across commands."""
        return SqlAlchemyUnitOfWork(self.session_factory)


def build_container(settings: Settings | None = None) -> Container:
    """Build the container and perform process-wide setup."""
    resolved = settings or get_settings()
    configure_logging(resolved)
    resolved.ensure_directories()
    return Container(settings=resolved, project_root=find_project_root())
