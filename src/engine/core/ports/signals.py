"""Signal emission port.

Stages record feedback without importing persistence, and tests assert on what
was emitted using a recording fake rather than by querying a database.

The typed helpers on :class:`SignalEmitter` matter more than they look. A
generic ``emit(signal)`` would push the burden of constructing correct rows onto
every call site, and the predictable result is that half the emissions omit
``polarity`` or invent a signal type with a typo. Named helpers make the common
cases impossible to get wrong while leaving the generic path available.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from engine.core.domain.enums import SubjectType
from engine.core.domain.signal import Signal


@runtime_checkable
class SignalEmitter(Protocol):
    """Records signals. Append-only by contract: there is no update or delete."""

    def emit(self, signal: Signal) -> Signal:
        """Record one signal."""
        ...

    def emit_many(self, signals: list[Signal]) -> int:
        """Record several signals, returning how many were written.

        Batched because a single ranking stage emits one signal per story and
        round-tripping each would dominate its runtime.
        """
        ...


@runtime_checkable
class SignalQuery(Protocol):
    """Read side of the signal log.

    Separated from emission because the write path is used by every stage while
    the read path is used only by the learning phase and analytics. Keeping them
    apart means a stage cannot accidentally depend on historical signals mid-run.
    """

    def for_subject(
        self,
        workspace_id: str,
        subject_type: SubjectType,
        subject_id: str,
    ) -> list[Signal]:
        """Everything that ever happened to one subject."""
        ...

    def by_type(
        self,
        workspace_id: str,
        signal_type: str,
        *,
        limit: int = 1000,
    ) -> list[Signal]:
        """Recent signals of one type, newest first."""
        ...

    def count_by_type(self, workspace_id: str) -> dict[str, int]:
        """Signal volume per type. Powers the funnel view and health checks."""
        ...


class SignalContext(Protocol):
    """Ambient identifiers every signal needs.

    Passed to emission helpers so call sites do not repeat account, workspace,
    actor and run on every emission.
    """

    account_id: str
    workspace_id: str
    actor_id: str
    run_id: str | None


def build_context(
    *,
    account_id: str,
    workspace_id: str,
    actor_id: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Construct the ambient fields shared by every signal in a run."""
    return {
        "account_id": account_id,
        "workspace_id": workspace_id,
        "actor_id": actor_id,
        "run_id": run_id,
    }
