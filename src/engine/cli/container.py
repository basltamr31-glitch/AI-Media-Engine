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

from engine.adapters.alerts import CompositeAlertSink, FileAlertSink, LoggingAlertSink
from engine.adapters.clock import SystemClock
from engine.adapters.config.policy_loader import PolicyLoader
from engine.adapters.enrichment.extractor import TrafilaturaExtractor
from engine.adapters.http import HttpxClient
from engine.adapters.llm.claude import AnthropicClient
from engine.adapters.llm.ollama import OllamaClient
from engine.adapters.packs.loader import FilesystemPackLoader
from engine.adapters.persistence.session import create_db_engine, create_session_factory
from engine.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWork
from engine.adapters.plugins.context import PluginContext
from engine.adapters.plugins.discovery import (
    GROUP_FETCHERS,
    GROUP_PARSERS,
    GROUP_SIGNALS,
    PluginLoader,
    PluginRecord,
)
from engine.adapters.politeness import PoliteHttpClient, RateLimiter, RobotsPolicy
from engine.adapters.scoring.registry import SignalRegistry
from engine.adapters.sources.archive import FileSystemRawArchive, NullRawArchive
from engine.adapters.sources.email.oauth import GoogleCredentialStore, credential_store_from
from engine.adapters.sources.registry import FetcherRegistry, ParserRegistry
from engine.core.ports.alerts import AlertSink
from engine.core.ports.clock import Clock
from engine.core.ports.enrichment import ContentExtractor
from engine.core.ports.http import HttpClient
from engine.core.ports.llm import LLMError, LLMPort
from engine.core.ports.packs import PackLoader
from engine.core.ports.scoring import SignalComputer
from engine.core.ports.sources import DocumentParser, RawDocumentArchive, SourceFetcher
from engine.observability.logging import configure_logging, get_logger
from engine.settings import Settings, get_settings

logger = get_logger(__name__)


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
    def packs(self) -> PackLoader:
        return FilesystemPackLoader(self.resolve_path(self.settings.packs_dir))

    @cached_property
    def http(self) -> HttpClient:
        """The client every fetcher gets: retrying, rate-limited and polite.

        Composed here rather than in the fetchers, so transport policy has one
        home and adding a fetcher cannot accidentally opt out of it.
        """
        transport = HttpxClient(
            timeout_seconds=self.settings.http_timeout_seconds,
            user_agent=self.settings.http_user_agent,
        )
        limiter = RateLimiter(min_interval_seconds=self.settings.http_min_interval_seconds)
        return PoliteHttpClient(
            transport,
            robots=RobotsPolicy(
                transport,
                user_agent=self.settings.http_user_agent,
                ttl_seconds=self.settings.http_robots_ttl_seconds,
                limiter=limiter,
            ),
            limiter=limiter,
            enforce_robots=self.settings.http_respect_robots,
        )

    @cached_property
    def alerts(self) -> AlertSink:
        """Where an unattended failure is reported.

        Both channels always: the log reaches whatever already collects the
        process's output, and the file survives a terminal nobody was watching.
        """
        return CompositeAlertSink(
            LoggingAlertSink(),
            FileAlertSink(self.resolve_path(self.settings.data_dir) / "alerts.jsonl"),
        )

    @cached_property
    def plugin_context(self) -> PluginContext:
        """What every plugin factory receives, built-in ones included."""
        return PluginContext(
            settings=self.settings,
            clock=self.clock,
            http=self.http,
            resolve_path=self.resolve_path,
        )

    @cached_property
    def plugin_loader(self) -> PluginLoader:
        return PluginLoader(self.plugin_context)

    @cached_property
    def credential_store(self) -> GoogleCredentialStore:
        return credential_store_from(self.plugin_context)

    @cached_property
    def extractor(self) -> ContentExtractor:
        return TrafilaturaExtractor(
            clock=self.clock,
            min_text_length=self.settings.enrich_min_text_length,
        )

    @cached_property
    def llm(self) -> LLMPort:
        """The configured language model.

        The only place a provider is named. Every stage above receives this as
        an :class:`~engine.core.ports.llm.LLMPort`, so moving from a local
        model to a hosted one is an environment variable rather than a change
        to any pipeline code.
        """
        if self.settings.llm_provider == "anthropic":
            key = self.settings.anthropic_api_key
            if key is None:
                raise LLMError(
                    "ENGINE_LLM_PROVIDER=anthropic but ENGINE_ANTHROPIC_API_KEY is unset",
                    code="missing_credentials",
                )
            return AnthropicClient(api_key=key.get_secret_value())
        return OllamaClient(
            base_url=self.settings.ollama_base_url,
            timeout_seconds=self.settings.ollama_timeout_seconds,
            context_tokens=self.settings.ollama_context_tokens,
        )

    @cached_property
    def raw_archive(self) -> RawDocumentArchive:
        if not self.settings.ingest_archive_raw:
            return NullRawArchive()
        return FileSystemRawArchive(self.resolve_path(self.settings.raw_archive_dir))

    @cached_property
    def parsers(self) -> ParserRegistry:
        """One parser per document format, discovered from installed packages.

        The engine ships no parsers until Phase 2; a third-party package can
        supply one today by declaring an ``engine.parsers`` entry point.
        """
        registry = ParserRegistry()
        self.plugin_loader.load(
            GROUP_PARSERS,
            expects=DocumentParser,
            register=registry.register,
        )
        return registry

    @cached_property
    def fetchers(self) -> FetcherRegistry:
        """One fetcher per transport, discovered from installed packages.

        Adding a *source* never touches this method, and since ADR 0017 adding
        a *transport* no longer touches it either: the engine's own three are
        declared in its ``pyproject.toml`` alongside anyone else's. Reddit,
        Hacker News and GitHub Releases remain configuration over the existing
        JSON fetcher.
        """
        registry = FetcherRegistry()
        self.plugin_loader.load(
            GROUP_FETCHERS,
            expects=SourceFetcher,
            register=registry.register,
        )
        return registry

    @cached_property
    def signal_computers(self) -> SignalRegistry:
        """One computer per ranking signal, discovered from installed packages.

        A vertical that ranks on ``cvss_severity`` ships the computer beside
        its pack rather than in this repository.
        """
        registry = SignalRegistry()
        self.plugin_loader.load(
            GROUP_SIGNALS,
            expects=SignalComputer,
            register=registry.register,
        )
        return registry

    def plugin_records(self) -> list[PluginRecord]:
        """Force discovery for every group and report the outcome per plugin.

        Building the registries is what performs discovery, so inspection has
        to go through the same path rather than a parallel one that could
        disagree with what the engine actually loaded.
        """
        fetchers, parsers = self.fetchers, self.parsers
        signals = self.signal_computers
        logger.debug(
            "plugin.inspect",
            fetchers=len(fetchers),
            parsers=len(parsers),
            signals=len(signals),
        )
        return self.plugin_loader.records

    def unit_of_work(self) -> SqlAlchemyUnitOfWork:
        """Create a fresh transactional scope. Never share one across commands."""
        return SqlAlchemyUnitOfWork(self.session_factory)


def build_container(settings: Settings | None = None) -> Container:
    """Build the container and perform process-wide setup."""
    resolved = settings or get_settings()
    configure_logging(resolved)
    resolved.ensure_directories()
    return Container(settings=resolved, project_root=find_project_root())
