"""Tests for feedback-driven weight tuning.

The tuner's value is in what it refuses to do. A confident proposal from twelve
posts would be worse than no tuner at all, because it would look like evidence.
So most of these tests are about restraint: thin data, flat signals, negative
correlations, and the damping that keeps one unusual fortnight from rewriting
the ranking.
"""

from __future__ import annotations

import pytest

from engine.core.domain.policy import ScoringProfile
from engine.core.domain.tuning import (
    Observation,
    correlation,
    propose_weights,
)

CURRENT = {"corroboration": 0.4, "recency": 0.3, "prominence": 0.3}


def observations(count: int, *, driver: str = "corroboration") -> list[Observation]:
    """Data where one signal cleanly drives the outcome and the others do not.

    Deterministic rather than random: a tuner test that occasionally proposes
    different weights teaches nothing about the tuner.
    """
    built: list[Observation] = []
    for index in range(count):
        position = index / max(count - 1, 1)
        signals = {
            "corroboration": position,
            # Alternating, so it is uncorrelated with the outcome rather than
            # merely weakly correlated.
            "recency": float(index % 2),
            "prominence": 1.0 - position,
        }
        built.append(
            Observation(story_id=f"sty_{index}", signals=signals, outcome=100.0 * signals[driver])
        )
    return built


class TestCorrelation:
    def test_a_perfect_positive_relationship(self):
        assert correlation([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)

    def test_a_perfect_negative_relationship(self):
        assert correlation([1.0, 2.0, 3.0], [6.0, 4.0, 2.0]) == pytest.approx(-1.0)

    def test_no_relationship_is_near_zero(self):
        """A symmetric V against a rising line: the deviations cancel exactly."""
        assert correlation([1.0, 2.0, 3.0, 4.0], [1.0, 0.0, 0.0, 1.0]) == pytest.approx(0.0)

    def test_a_flat_series_is_undefined_not_zero(self):
        """A signal that never varied tells us nothing; that is not the same
        as telling us there is no relationship."""
        assert correlation([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None

    def test_too_few_points_is_undefined(self):
        assert correlation([1.0], [1.0]) is None

    def test_mismatched_lengths_are_undefined(self):
        assert correlation([1.0, 2.0], [1.0]) is None

    def test_the_result_stays_inside_the_valid_range(self):
        """Floating point can otherwise land a hair outside and fail a validator."""
        value = correlation([0.1, 0.2, 0.3], [0.1, 0.2, 0.3])
        assert value is not None and -1.0 <= value <= 1.0


class TestRefusals:
    def test_thin_data_produces_no_proposal(self):
        proposal = propose_weights(observations(5), CURRENT)

        assert proposal.is_actionable is False
        assert "5 measured" in proposal.reason

    def test_a_refusal_still_reports_the_current_weights(self):
        """The operator asked a question; 'not yet' should still show the state."""
        proposal = propose_weights(observations(5), CURRENT)

        assert proposal.weights == CURRENT

    def test_one_story_reads_as_singular(self):
        assert "1 measured story;" in propose_weights(observations(1), CURRENT).reason

    def test_no_weighted_signals_is_refused(self):
        proposal = propose_weights(observations(50), {})

        assert proposal.is_actionable is False
        assert "nothing to tune" in proposal.reason

    def test_nothing_correlating_leaves_the_weights_alone(self):
        """Every signal flat: there is no evidence, so nothing should move."""
        flat = [
            Observation(story_id=str(index), signals=dict.fromkeys(CURRENT, 1.0), outcome=index)
            for index in range(30)
        ]

        proposal = propose_weights(flat, CURRENT)

        assert proposal.is_actionable is False
        assert proposal.weights == CURRENT

    def test_only_negative_correlations_leave_the_weights_alone(self):
        """Flooring at zero would otherwise erase every weight at once."""
        against = [
            Observation(
                story_id=str(index),
                signals={"corroboration": float(index), "recency": float(index)},
                outcome=-float(index),
            )
            for index in range(30)
        ]

        proposal = propose_weights(against, {"corroboration": 0.5, "recency": 0.5})

        assert proposal.is_actionable is False
        assert proposal.weights == {"corroboration": 0.5, "recency": 0.5}


class TestProposals:
    def test_the_driving_signal_gains_weight(self):
        proposal = propose_weights(observations(30), CURRENT)

        assert proposal.is_actionable
        gained = next(s for s in proposal.signals if s.key == "corroboration")
        assert gained.proposed_weight > gained.current_weight
        assert gained.direction == "up"

    def test_an_anticorrelated_signal_loses_weight(self):
        proposal = propose_weights(observations(30), CURRENT)

        lost = next(s for s in proposal.signals if s.key == "prominence")
        assert lost.proposed_weight < lost.current_weight

    def test_the_proposal_is_a_valid_scoring_profile(self):
        """It has to survive the validator it will be pasted into."""
        proposal = propose_weights(observations(30), CURRENT)

        profile = ScoringProfile(weights=proposal.weights)

        assert profile.signal_keys == set(CURRENT)

    def test_the_weights_sum_to_one_after_rounding(self):
        """Rounding for legibility must not break the sum-to-one rule."""
        proposal = propose_weights(observations(37), CURRENT)

        assert sum(proposal.weights.values()) == pytest.approx(1.0)

    def test_no_weight_is_negative(self):
        proposal = propose_weights(observations(30), CURRENT)

        assert all(weight >= 0.0 for weight in proposal.weights.values())

    def test_the_strongest_signal_is_identified(self):
        strongest = propose_weights(observations(30), CURRENT).strongest

        assert strongest is not None
        assert strongest.key == "corroboration"

    def test_the_sample_size_is_reported(self):
        assert propose_weights(observations(30), CURRENT).samples == 30

    def test_the_metric_is_carried_through(self):
        """Weights fitted against saves mean something else than against likes."""
        assert propose_weights(observations(30), CURRENT, metric="saves").metric == "saves"


class TestDamping:
    def test_zero_damping_keeps_the_current_weights(self):
        proposal = propose_weights(observations(30), CURRENT, damping=0.0)

        assert proposal.weights == pytest.approx(CURRENT)

    def test_full_damping_follows_the_data(self):
        proposal = propose_weights(observations(30), CURRENT, damping=1.0)

        driver = next(s for s in proposal.signals if s.key == "corroboration")
        assert driver.proposed_weight > 0.5

    def test_damping_limits_how_far_one_fit_can_move(self):
        """One unusual fortnight should nudge the ranking, not replace it."""
        gentle = propose_weights(observations(30), CURRENT, damping=0.1)
        aggressive = propose_weights(observations(30), CURRENT, damping=0.9)

        gentle_move = gentle.weights["corroboration"] - CURRENT["corroboration"]
        aggressive_move = aggressive.weights["corroboration"] - CURRENT["corroboration"]
        assert gentle_move < aggressive_move


class TestReporting:
    def test_a_flat_signal_is_marked_as_never_varied(self):
        """Distinguished from a genuine zero correlation, which means something else."""
        data = [
            Observation(
                story_id=str(index),
                signals={"corroboration": float(index), "recency": 1.0},
                outcome=float(index),
            )
            for index in range(30)
        ]

        proposal = propose_weights(data, {"corroboration": 0.5, "recency": 0.5})

        flat = next(s for s in proposal.signals if s.key == "recency")
        assert flat.varied is False

    def test_a_missing_signal_does_not_crash_the_fit(self):
        """Older scores may predate a signal the workspace now weights."""
        data = [
            Observation(story_id=str(index), signals={"corroboration": float(index)}, outcome=index)
            for index in range(30)
        ]

        proposal = propose_weights(data, CURRENT)

        assert proposal.is_actionable
        assert set(proposal.weights) == set(CURRENT)

    def test_a_barely_moved_weight_reads_as_held(self):
        proposal = propose_weights(observations(30), CURRENT, damping=0.001)

        assert all(signal.direction == "hold" for signal in proposal.signals)
