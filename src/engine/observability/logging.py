"""Structured logging.

Console-rendered for humans locally, JSON in production. JSON matters earlier
than it seems: the moment this runs on a schedule rather than in a terminal,
"why did Tuesday's run pick those stories?" is answerable only if every stage
emitted queryable, structured events.

Context (workspace, run, stage) is bound once and attached to every subsequent
event automatically, so no call site has to remember to include it.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from engine.settings import Settings

_configured = False


def configure_logging(settings: Settings) -> None:
    """Configure structlog and the stdlib logging bridge. Idempotent."""
    global _configured
    if _configured:
        return

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=getattr(logging, settings.log_level),
    )

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.log_format == "json":
        renderer: Any = structlog.processors.JSONRenderer()
        shared_processors.append(structlog.processors.format_exc_info)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)
        shared_processors.append(structlog.processors.ExceptionPrettyPrinter())

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, settings.log_level)),
        # Must be the stdlib factory, not PrintLoggerFactory: `add_logger_name`
        # reads `logger.name`, which only stdlib loggers expose. Routing through
        # stdlib logging also means libraries that log normally land in the same
        # stream with the same formatting.
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound logger for ``name``."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


def bind_run_context(**values: Any) -> None:
    """Attach values (workspace_id, run_id, ...) to every subsequent log event."""
    structlog.contextvars.bind_contextvars(**values)


def clear_run_context() -> None:
    """Drop all bound context. Call at the end of a run."""
    structlog.contextvars.clear_contextvars()
