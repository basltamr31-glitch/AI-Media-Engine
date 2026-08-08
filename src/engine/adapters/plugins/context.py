"""What a plugin factory is handed.

An entry point resolves to a callable taking exactly one argument -- this
context -- and returning the implementation. One uniform signature for every
group, so a third-party fetcher is constructed the same way the built-in ones
are, and so adding a shared dependency later means adding a field here rather
than revisiting every plugin's constructor.

Deliberately small. Everything on it is already a port or configuration, so a
plugin can be written against it without importing anything from the engine's
infrastructure.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from engine.core.ports.clock import Clock
from engine.core.ports.http import HttpClient
from engine.settings import Settings


@dataclass(frozen=True)
class PluginContext:
    """Dependencies available to any plugin factory."""

    settings: Settings
    clock: Clock
    http: HttpClient
    resolve_path: Callable[[Path], Path]
    """Anchors a relative configured path to the project root."""
