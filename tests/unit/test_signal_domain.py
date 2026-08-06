"""Tests for the signal domain model (ADR 0012).

The invariants here protect a dataset that cannot be rebuilt if it is corrupted,
so they are stricter than they would be for ordinary state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from engine.core.domain.enums import SignalPolarity, SubjectType
from engine.core.domain.ids import IdPrefix, is_valid_id
from engine.core.domain.signal import (
    BUILT_IN_SIGNAL_TYPES,
    SIGNAL_CONTENT_EDITED,
    SIGNAL_ENGAGEMENT,
    SIGNAL_STORY_REJECTED,
    Signal,
)

BASE = {
    "account_id": "acc_x",
    "workspace_id": "ws_x",
    "actor_id": "act_x",
    "subject_type": SubjectType.STORY,
    "subject_id": "sty_x",
}


class TestConstruction:
    def test_generates_a_prefixed_id(self):
        signal = Signal(**BASE, signal_type=SIGNAL_STORY_REJECTED, label="off-topic")
        assert is_valid_id(signal.id, IdPrefix.SIGNAL)

    def test_accepts_a_numeric_outcome(self):
        signal = Signal(**BASE, signal_type=SIGNAL_ENGAGEMENT, value=1420.0)
        assert signal.value == 1420.0

    def test_accepts_a_categorical_outcome(self):
        signal = Signal(**BASE, signal_type=SIGNAL_STORY_REJECTED, label="already-covered")
        assert signal.label == "already-covered"

    def test_accepts_context_only(self):
        signal = Signal(**BASE, signal_type="custom.thing", context={"detail": "x"})
        assert signal.context == {"detail": "x"}

    def test_signal_type_is_an_open_vocabulary(self):
        """A vertical defines its own signals without a migration (ADR 0015)."""
        signal = Signal(**BASE, signal_type="cve.exploited_in_wild", value=1.0)
        assert signal.signal_type not in BUILT_IN_SIGNAL_TYPES

    def test_defaults_to_neutral_polarity(self):
        signal = Signal(**BASE, signal_type=SIGNAL_ENGAGEMENT, value=1.0)
        assert signal.polarity is SignalPolarity.NEUTRAL


class TestInvariants:
    def test_a_signal_with_no_payload_is_rejected(self):
        """A signal recording nothing learnable is a bug, not a data point."""
        with pytest.raises(ValidationError, match="carries no value, label or context"):
            Signal(**BASE, signal_type=SIGNAL_STORY_REJECTED)

    def test_recorded_at_cannot_precede_occurred_at(self):
        now = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
        with pytest.raises(ValidationError, match="cannot precede"):
            Signal(
                **BASE,
                signal_type=SIGNAL_ENGAGEMENT,
                value=1.0,
                occurred_at=now,
                recorded_at=now - timedelta(hours=1),
            )

    def test_signals_are_immutable(self):
        """Append-only is what makes the log usable as evidence.

        mypy rejects this assignment statically, which is the stronger
        guarantee; the ignore is here so the runtime behaviour is also pinned.
        """
        signal = Signal(**BASE, signal_type=SIGNAL_ENGAGEMENT, value=1.0)
        with pytest.raises(ValidationError):
            signal.value = 2.0  # type: ignore[misc]


class TestTiming:
    def test_latency_separates_when_it_happened_from_when_we_learned(self):
        """Engagement is pulled hours later; collapsing the two corrupts timing analysis."""
        occurred = datetime(2026, 8, 6, 9, 0, tzinfo=UTC)
        signal = Signal(
            **BASE,
            signal_type=SIGNAL_ENGAGEMENT,
            value=500.0,
            occurred_at=occurred,
            recorded_at=occurred + timedelta(hours=6),
        )
        assert signal.latency_seconds == 6 * 3600

    def test_same_instant_means_zero_latency(self):
        now = datetime(2026, 8, 6, 9, 0, tzinfo=UTC)
        signal = Signal(
            **BASE,
            signal_type=SIGNAL_CONTENT_EDITED,
            value=42.0,
            occurred_at=now,
            recorded_at=now,
        )
        assert signal.latency_seconds == 0
