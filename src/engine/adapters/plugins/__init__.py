"""Plugin discovery (ADR 0017)."""

from engine.adapters.plugins.context import PluginContext
from engine.adapters.plugins.discovery import (
    GROUP_FETCHERS,
    GROUP_PARSERS,
    GROUP_SIGNALS,
    PLUGIN_GROUPS,
    PluginLoader,
    PluginRecord,
    PluginStatus,
    installed_entry_points,
)

__all__ = [
    "GROUP_FETCHERS",
    "GROUP_PARSERS",
    "GROUP_SIGNALS",
    "PLUGIN_GROUPS",
    "PluginContext",
    "PluginLoader",
    "PluginRecord",
    "PluginStatus",
    "installed_entry_points",
]
