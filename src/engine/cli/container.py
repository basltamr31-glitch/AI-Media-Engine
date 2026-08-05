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
from engine.adapters.persistence.session import create_db_engine, create_session_factory
from engine.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWork
from engine.adapters.sources.registry import FetcherRegistry, ParserRegistry
from engine.core.ports.clock import Clock
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
        config_dir = self.settings.config_dir
        if not config_dir.is_absolute():
            config_dir = self.project_root / config_dir
        return PolicyLoader(config_dir)

    @cached_property
    def parsers(self) -> ParserRegistry:
        # Populated in Phase 1; the registry exists now so wiring has a home.
        return ParserRegistry()

    @cached_property
    def fetchers(self) -> FetcherRegistry:
        return FetcherRegistry()

    def unit_of_work(self) -> SqlAlchemyUnitOfWork:
        """Create a fresh transactional scope. Never share one across commands."""
        return SqlAlchemyUnitOfWork(self.session_factory)


def build_container(settings: Settings | None = None) -> Container:
    """Build the container and perform process-wide setup."""
    resolved = settings or get_settings()
    configure_logging(resolved)
    resolved.ensure_directories()
    return Container(settings=resolved, project_root=find_project_root())
