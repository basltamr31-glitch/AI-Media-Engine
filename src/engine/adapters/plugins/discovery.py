"""Plugin discovery through ``importlib.metadata`` entry points (ADR 0017).

Registries used to be populated by the composition root importing concrete
classes. That caps the ecosystem at what this repository contains: a YouTube
transport or a pharma-specific parser would need a pull request against the
core engine. Here, installing a package is enough.

Three properties make this safe rather than merely clever:

* **Built-ins arrive the same way.** The engine's own fetchers are declared in
  its own ``pyproject.toml``. If discovery breaks, it breaks in development
  rather than only for third parties.
* **Failures are isolated and loud.** A plugin that fails to import, blows up
  in its factory, returns the wrong type or collides with an existing key is
  logged and skipped. One broken third-party package must not stop a
  customer's pipeline.
* **Discovery is inspectable.** Every entry point examined produces a
  :class:`PluginRecord`, which is what ``engine plugins list`` renders. An
  extension mechanism nobody can debug is worse than no extension mechanism.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Final

from engine.adapters.plugins.context import PluginContext
from engine.observability.logging import get_logger

logger = get_logger(__name__)

#: Entry-point group for :class:`~engine.core.ports.sources.SourceFetcher`
#: implementations -- one per transport.
GROUP_FETCHERS: Final = "engine.fetchers"

#: Entry-point group for :class:`~engine.core.ports.sources.DocumentParser`
#: implementations -- one per document format.
GROUP_PARSERS: Final = "engine.parsers"

#: Entry-point group for :class:`~engine.core.ports.scoring.SignalComputer`
#: implementations -- one per ranking signal a pack declares.
GROUP_SIGNALS: Final = "engine.signals"

#: Groups the engine currently consumes. Further groups (renderers,
#: destinations) are added when the registry that consumes them exists; an
#: advertised group nothing reads is a promise the engine does not keep.
PLUGIN_GROUPS: Final = (GROUP_FETCHERS, GROUP_PARSERS, GROUP_SIGNALS)

_UNKNOWN_DISTRIBUTION: Final = "unknown"


class PluginStatus(StrEnum):
    """Outcome of examining one entry point."""

    LOADED = "loaded"
    FAILED = "failed"


@dataclass(frozen=True)
class PluginRecord:
    """What discovery found at one entry point, and what became of it."""

    group: str
    name: str
    value: str
    distribution: str = _UNKNOWN_DISTRIBUTION
    version: str = ""
    status: PluginStatus = PluginStatus.LOADED
    error: str | None = None

    @property
    def provider(self) -> str:
        """Installed package that supplied this plugin, for display."""
        return f"{self.distribution} {self.version}".strip()

    @property
    def failed(self) -> bool:
        return self.status is PluginStatus.FAILED


#: Where entry points come from. Injectable so discovery can be tested without
#: installing packages into the interpreter running the tests.
EntryPointSource = Callable[[str], Iterable[EntryPoint]]


def installed_entry_points(group: str) -> Iterable[EntryPoint]:
    """Entry points declared by packages installed in this environment."""
    return entry_points(group=group)


class PluginLoader:
    """Resolves entry points into live implementations, isolating failures."""

    def __init__(
        self,
        context: PluginContext,
        *,
        source: EntryPointSource = installed_entry_points,
    ) -> None:
        self._context = context
        self._source = source
        self._records: dict[str, list[PluginRecord]] = {}

    def load(
        self,
        group: str,
        *,
        expects: type,
        register: Callable[[Any], None],
    ) -> list[PluginRecord]:
        """Load every plugin in ``group`` and hand each one to ``register``.

        Registration happens inside the per-plugin error boundary on purpose: a
        duplicate key is a plugin problem, not an engine problem, and should
        cost that one plugin rather than the whole registry.

        Args:
            group: Entry-point group, e.g. ``engine.fetchers``.
            expects: Protocol the loaded object must satisfy. Checked here so a
                wrong type fails at startup rather than mid-run.
            register: Called with each loaded implementation.

        Returns:
            One record per entry point examined, in name order so that output
            and registration order do not depend on installation order.
        """
        records = [
            self._load_one(group, entry_point, expects=expects, register=register)
            for entry_point in sorted(self._source(group), key=lambda point: point.name)
        ]
        self._records[group] = records
        return records

    @property
    def records(self) -> list[PluginRecord]:
        """Every record from every group loaded so far."""
        return [record for group in PLUGIN_GROUPS for record in self._records.get(group, [])]

    def records_for(self, group: str) -> list[PluginRecord]:
        return list(self._records.get(group, []))

    def _load_one(
        self,
        group: str,
        entry_point: EntryPoint,
        *,
        expects: type,
        register: Callable[[Any], None],
    ) -> PluginRecord:
        distribution, version = _provenance(entry_point)
        try:
            factory = entry_point.load()
            instance = factory(self._context)
            if not isinstance(instance, expects):
                msg = (
                    f"{entry_point.value} returned {type(instance).__name__}, "
                    f"which does not implement {expects.__name__}"
                )
                raise TypeError(msg)
            register(instance)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "plugin.failed",
                group=group,
                name=entry_point.name,
                value=entry_point.value,
                distribution=distribution,
                error=reason,
            )
            return PluginRecord(
                group=group,
                name=entry_point.name,
                value=entry_point.value,
                distribution=distribution,
                version=version,
                status=PluginStatus.FAILED,
                error=reason,
            )

        logger.debug(
            "plugin.loaded",
            group=group,
            name=entry_point.name,
            value=entry_point.value,
            distribution=distribution,
        )
        return PluginRecord(
            group=group,
            name=entry_point.name,
            value=entry_point.value,
            distribution=distribution,
            version=version,
            status=PluginStatus.LOADED,
        )


def _provenance(entry_point: EntryPoint) -> tuple[str, str]:
    """Which installed package declared this entry point.

    Synthetic entry points (tests, programmatic registration) have no
    distribution behind them, which is reported rather than hidden.
    """
    distribution = getattr(entry_point, "dist", None)
    if distribution is None:
        return _UNKNOWN_DISTRIBUTION, ""
    return distribution.metadata["Name"] or _UNKNOWN_DISTRIBUTION, distribution.version
