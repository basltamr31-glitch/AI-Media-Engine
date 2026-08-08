"""Signal computer registry.

Keyed by ranking-signal key, exactly as the pack declares it. A pack that
declares a signal nobody computes is a configuration error worth reporting --
the weight would silently vanish from every score otherwise.
"""

from __future__ import annotations

from engine.core.ports.scoring import SignalComputer


class SignalComputerNotFoundError(LookupError):
    """Raised when a declared ranking signal has no computer behind it."""


class SignalRegistry:
    """Maps a ranking-signal key to the computer that measures it."""

    def __init__(self) -> None:
        self._computers: dict[str, SignalComputer] = {}

    def register(self, computer: SignalComputer, *, replace: bool = False) -> None:
        key = computer.key
        if key in self._computers and not replace:
            msg = f"signal computer for {key!r} is already registered"
            raise ValueError(msg)
        self._computers[key] = computer

    def get(self, key: str) -> SignalComputer:
        try:
            return self._computers[key]
        except KeyError as exc:
            available = ", ".join(sorted(self._computers)) or "<none>"
            msg = f"no computer registered for signal {key!r}; available: {available}"
            raise SignalComputerNotFoundError(msg) from exc

    def keys(self) -> list[str]:
        return sorted(self._computers)

    def missing_for(self, signal_keys: set[str]) -> list[str]:
        """Declared signals with nothing to measure them.

        Reported rather than raised: a pack that declares a signal ahead of its
        computer should still rank on the rest, loudly.
        """
        return sorted(signal_keys - set(self._computers))

    def __contains__(self, key: object) -> bool:
        return key in self._computers

    def __len__(self) -> int:
        return len(self._computers)
