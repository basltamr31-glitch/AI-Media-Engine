"""Fitting ranking weights to what actually performed.

This is the payoff ADR 0016 was written for. Every score kept its per-signal
``raw`` measurements, and Phase 5 recorded what each published post achieved,
so the training inputs already exist: a feature matrix and an outcome column,
accumulated as a by-product of ordinary use.

**Correlation, not regression.** The obvious move is least squares. It is the
wrong one here, and would stay wrong for a long time: with seven signals and a
few dozen published posts, a fitted regression has enough freedom to explain
noise exactly, and it would produce confident, unstable, uninterpretable
weights that change wildly with each new week of data. Correlation per signal is
weaker and far more honest -- it answers "did stories that scored high on this
signal do better?", one signal at a time, with a sample size attached.

Three deliberate conservatisms, each of which makes the tuner less clever and
more trustworthy:

* **It refuses on thin data** rather than fitting whatever it has.
* **It damps** towards the current weights, so one unusual fortnight nudges the
  ranking instead of replacing it.
* **It proposes.** Nothing here writes a config. Ranking is editorial judgement,
  and handing it to an unsupervised statistic over a few dozen posts would trade
  the most valuable thing in the system for arithmetic.

Pure functions, no I/O, no model calls, no numpy. The cost rule applies with
full force: an engine that ranks by algorithm must not learn by API call.
"""

from __future__ import annotations

from math import sqrt
from typing import Final

from pydantic import Field

from engine.core.domain.base import ImmutableModel

#: Below this many published-and-measured stories, no proposal is made. Seven
#: signals fitted on ten posts is numerology; the number is low enough to be
#: reachable in a first quarter and high enough that one viral post cannot
#: rewrite the ranking on its own.
MIN_SAMPLES: Final = 20

#: How far towards the evidence a single proposal may move. At 0.3 a signal
#: with perfect correlation gains under a third of the distance to its implied
#: weight, so the ranking converges over months of data rather than lurching
#: after one good week.
DEFAULT_DAMPING: Final = 0.3


class SignalCorrelation(ImmutableModel):
    """How one ranking signal related to measured engagement."""

    key: str
    correlation: float = Field(
        ge=-1.0,
        le=1.0,
        description=(
            "Pearson correlation between the signal's raw value and the "
            "outcome. Zero when the signal never varied, which is not evidence "
            "of no relationship -- it is an absence of evidence, and the two "
            "are distinguished by `varied`."
        ),
    )
    varied: bool = Field(
        default=True,
        description="False when every story scored the same, making correlation undefined.",
    )
    current_weight: float = Field(ge=0.0, le=1.0)
    proposed_weight: float = Field(ge=0.0, le=1.0)

    @property
    def change(self) -> float:
        return self.proposed_weight - self.current_weight

    @property
    def direction(self) -> str:
        """Which way this signal is being nudged, for a report."""
        if abs(self.change) < 0.005:
            return "hold"
        return "up" if self.change > 0 else "down"


class WeightProposal(ImmutableModel):
    """A suggested re-weighting, and the evidence for it."""

    metric: str = Field(description="Which engagement metric the outcome column came from.")
    samples: int = Field(ge=0, description="Published, measured stories the fit is based on.")
    signals: list[SignalCorrelation] = Field(default_factory=list)
    is_actionable: bool = False
    reason: str = Field(
        default="",
        description="Why no proposal was made. Empty when one was.",
    )

    @property
    def weights(self) -> dict[str, float]:
        """The proposed profile, ready to be validated against the pack."""
        return {signal.key: signal.proposed_weight for signal in self.signals}

    @property
    def strongest(self) -> SignalCorrelation | None:
        """The signal most associated with engagement, for a one-line summary."""
        varied = [signal for signal in self.signals if signal.varied]
        if not varied:
            return None
        return max(varied, key=lambda signal: signal.correlation)


class Observation(ImmutableModel):
    """One published story: what it scored on, and what it achieved."""

    story_id: str = ""
    signals: dict[str, float] = Field(
        default_factory=dict,
        description="The score's raw per-signal measurements.",
    )
    outcome: float = Field(description="The engagement value being fitted against.")


def correlation(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation, or None when it is undefined.

    Undefined -- rather than zero -- when either series never varies. A signal
    that scored 1.0 on every story tells us nothing about engagement, and
    reporting that as "no correlation" would silently demote it as though the
    data had spoken.
    """
    count = len(xs)
    if count < 2 or count != len(ys):
        return None

    mean_x = sum(xs) / count
    mean_y = sum(ys) / count
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]

    covariance = sum(a * b for a, b in zip(dx, dy, strict=True))
    spread_x = sqrt(sum(a * a for a in dx))
    spread_y = sqrt(sum(b * b for b in dy))
    if spread_x == 0.0 or spread_y == 0.0:
        return None

    # Clamped because floating point can land a hair outside [-1, 1] on
    # perfectly correlated inputs, and a validator would then reject a
    # mathematically correct answer.
    return max(-1.0, min(1.0, covariance / (spread_x * spread_y)))


def propose_weights(
    observations: list[Observation],
    current: dict[str, float],
    *,
    metric: str = "engagement",
    min_samples: int = MIN_SAMPLES,
    damping: float = DEFAULT_DAMPING,
) -> WeightProposal:
    """Suggest new ranking weights from measured engagement.

    Signals correlating positively with engagement gain weight in proportion to
    how strongly; the result is damped towards ``current`` and renormalised to
    sum to 1.0, so it remains a valid scoring profile.

    Negative correlations are floored at zero rather than becoming negative
    weights. A signal that is *bad* for engagement is interesting, and it is
    reported -- but a negative weight would make the scorer actively reward the
    opposite, which is a much stronger claim than a few dozen posts support.
    """
    if not current:
        return WeightProposal(
            metric=metric,
            samples=len(observations),
            reason="the workspace weights no ranking signals, so there is nothing to tune",
        )

    if len(observations) < min_samples:
        return WeightProposal(
            metric=metric,
            samples=len(observations),
            signals=_unchanged(current),
            reason=(
                f"only {len(observations)} measured "
                f"{'story' if len(observations) == 1 else 'stories'}; "
                f"{min_samples} are needed before weights mean anything"
            ),
        )

    outcomes = [observation.outcome for observation in observations]
    measured: dict[str, float | None] = {
        key: correlation(
            [observation.signals.get(key, 0.0) for observation in observations],
            outcomes,
        )
        for key in current
    }

    # Only positive, well-defined correlations earn a share. If none do, the
    # data says nothing usable and the current weights stand.
    shares = {key: value for key, value in measured.items() if value is not None and value > 0.0}
    if not shares:
        return WeightProposal(
            metric=metric,
            samples=len(observations),
            signals=_unchanged(current, measured),
            reason="no signal correlated positively with engagement; weights left alone",
        )

    total_share = sum(shares.values())
    blended = {
        key: (1.0 - damping) * weight + damping * (shares.get(key, 0.0) / total_share)
        for key, weight in current.items()
    }
    proposed = _normalised(blended)

    return WeightProposal(
        metric=metric,
        samples=len(observations),
        signals=[
            SignalCorrelation(
                key=key,
                correlation=measured[key] or 0.0,
                varied=measured[key] is not None,
                current_weight=weight,
                proposed_weight=proposed[key],
            )
            for key, weight in current.items()
        ],
        is_actionable=True,
    )


def _unchanged(
    current: dict[str, float],
    measured: dict[str, float | None] | None = None,
) -> list[SignalCorrelation]:
    """Report the signals with no change proposed."""
    measured = measured or {}
    return [
        SignalCorrelation(
            key=key,
            correlation=measured.get(key) or 0.0,
            varied=measured.get(key) is not None if key in measured else True,
            current_weight=weight,
            proposed_weight=weight,
        )
        for key, weight in current.items()
    ]


def _normalised(weights: dict[str, float]) -> dict[str, float]:
    """Scale to sum to 1.0, which a scoring profile requires.

    Rounded to four places so the proposal is legible in a config file, then
    corrected on the largest weight to absorb the rounding error -- otherwise
    the printed profile fails the very validator it is meant to satisfy.
    """
    total = sum(weights.values())
    if total <= 0.0:
        share = 1.0 / len(weights)
        return dict.fromkeys(weights, share)

    scaled = {key: round(value / total, 4) for key, value in weights.items()}
    drift = round(1.0 - sum(scaled.values()), 4)
    if drift:
        largest = max(scaled, key=lambda key: scaled[key])
        scaled[largest] = round(scaled[largest] + drift, 4)
    return scaled
