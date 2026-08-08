"""Tests for the open scoring profile and pack-declared ranking signals (ADR 0016).

The property under test: adding a ranking signal is a pack entry, not a domain
model edit plus a config migration plus a redeploy. The old five-field model
with ``extra="forbid"`` made that impossible, and made per-vertical ranking
impossible with it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from engine.adapters.packs.loader import FilesystemPackLoader
from engine.core.domain.pack import DomainPack, Normaliser, RankingSignal
from engine.core.domain.policy import EditorialPolicy, ScoringProfile
from engine.core.ports.packs import PackLoadError
from tests.unit.test_pack import write_pack


class TestScoringProfile:
    def test_weights_must_sum_to_one(self):
        with pytest.raises(ValidationError, match=r"must sum to 1\.0"):
            ScoringProfile(weights={"a": 0.5, "b": 0.2})

    def test_a_tiny_rounding_error_is_tolerated(self):
        """Floating point never lands exactly on 1.0; a typo is not rounding noise.

        These three weights really do sum to 0.9999999999999999, so the test
        exercises the tolerance rather than merely asserting that 1.0 == 1.0.
        Most tidy weightings (0.1/0.2/0.7) happen to land exactly on 1.0, which
        is why the guard below pins the fixture.
        """
        weights = {"a": 0.01, "b": 0.29, "c": 0.7}
        assert sum(weights.values()) != 1.0, "fixture no longer exercises the tolerance"

        profile = ScoringProfile(weights=weights)

        assert profile.signal_keys == {"a", "b", "c"}

    @pytest.mark.parametrize("weight", [-0.1, 1.5])
    def test_weights_outside_zero_to_one_are_rejected(self, weight: float):
        with pytest.raises(ValidationError, match="between 0 and 1"):
            ScoringProfile(weights={"a": weight, "b": 1 - weight})

    def test_an_empty_profile_is_valid_and_means_inherit(self):
        profile = ScoringProfile()
        assert profile.is_empty
        assert profile.signal_keys == set()

    def test_weight_of_an_unweighted_signal_is_zero(self):
        assert ScoringProfile(weights={"a": 1.0}).weight_of("b") == 0.0


class TestValidationAgainstAPack:
    def test_a_matching_profile_passes(self):
        profile = ScoringProfile(weights={"a": 0.5, "b": 0.5})

        profile.validate_against({"a", "b", "c"})  # must not raise

        assert profile.signal_keys == {"a", "b"}

    def test_an_unknown_signal_is_rejected(self):
        """A misspelled key would otherwise contribute zero, silently."""
        profile = ScoringProfile(weights={"corroboration": 0.5, "corroboraton": 0.5})
        with pytest.raises(ValueError, match="unknown ranking signal"):
            profile.validate_against({"corroboration", "recency"})

    def test_the_error_lists_what_is_available(self):
        profile = ScoringProfile(weights={"ghost": 1.0})
        with pytest.raises(ValueError, match="available: recency"):
            profile.validate_against({"recency"})

    def test_the_error_names_the_pack(self):
        profile = ScoringProfile(weights={"ghost": 1.0})
        with pytest.raises(ValueError, match="in pack 'widgets'"):
            profile.validate_against({"recency"}, pack_key="widgets")

    def test_a_profile_may_weight_a_subset_of_declared_signals(self):
        """Declaring a signal does not oblige a workspace to weight it."""
        profile = ScoringProfile(weights={"a": 1.0})

        profile.validate_against({"a", "b", "c"})  # must not raise

        assert profile.signal_keys == {"a"}
        assert profile.weight_of("b") == 0.0


class TestPackRankingSignals:
    def test_default_weights_must_sum_to_one(self):
        """A pack ships ranking-ready or not at all."""
        with pytest.raises(ValidationError, match=r"must sum to 1\.0"):
            DomainPack(
                key="widgets",
                version=1,
                name="W",
                ranking_signals=[
                    RankingSignal(key="a", name="A", default_weight=0.5),
                    RankingSignal(key="b", name="B", default_weight=0.2),
                ],
            )

    def test_duplicate_signal_keys_are_rejected(self):
        with pytest.raises(ValidationError, match="duplicate ranking signal keys"):
            DomainPack(
                key="widgets",
                version=1,
                name="W",
                ranking_signals=[
                    RankingSignal(key="a", name="A", default_weight=0.5),
                    RankingSignal(key="a", name="Also A", default_weight=0.5),
                ],
            )

    def test_a_pack_with_no_signals_is_valid(self):
        """Ranking arrives in Phase 2; a pack need not declare signals yet."""
        assert DomainPack(key="widgets", version=1, name="W").ranking_signals == []

    def test_the_default_profile_is_built_from_declared_weights(self):
        pack = DomainPack(
            key="widgets",
            version=1,
            name="W",
            ranking_signals=[
                RankingSignal(key="a", name="A", default_weight=0.7),
                RankingSignal(key="b", name="B", default_weight=0.3),
            ],
        )
        assert pack.default_scoring_profile.weights == {"a": 0.7, "b": 0.3}
        pack.default_scoring_profile.validate_against(pack.ranking_signal_keys)

    def test_signal_lookup(self):
        pack = DomainPack(
            key="widgets",
            version=1,
            name="W",
            ranking_signals=[RankingSignal(key="a", name="A", default_weight=1.0)],
        )
        assert pack.ranking_signal("a") is not None
        assert pack.ranking_signal("missing") is None

    def test_normaliser_and_parameters_survive_the_round_trip(self, tmp_path):
        write_pack(
            tmp_path,
            "widgets",
            """
key: widgets
version: 1
name: W
ranking_signals:
  - key: attention
    name: Attention
    normaliser: logarithmic
    default_weight: 1.0
    parameters: {ceiling: 1000}
""",
        )
        signal = FilesystemPackLoader(tmp_path).load("widgets").ranking_signal("attention")
        assert signal is not None
        assert signal.normaliser is Normaliser.LOGARITHMIC
        assert signal.parameters == {"ceiling": 1000}

    def test_an_unknown_normaliser_is_rejected(self, tmp_path):
        write_pack(
            tmp_path,
            "widgets",
            """
key: widgets
version: 1
name: W
ranking_signals:
  - {key: a, name: A, normaliser: telepathy, default_weight: 1.0}
""",
        )
        with pytest.raises(PackLoadError, match="is invalid"):
            FilesystemPackLoader(tmp_path).load("widgets")


class TestVerticalSpecificSignals:
    def test_a_pack_can_declare_signals_no_enum_ever_contained(self, tmp_path):
        """The whole point: cybersecurity ranks on things AI news never will."""
        write_pack(
            tmp_path,
            "cybersecurity",
            """
key: cybersecurity
version: 1
name: Threat Intelligence
ranking_signals:
  - key: cvss_severity
    name: CVSS severity
    normaliser: linear
    default_weight: 0.5
    parameters: {ceiling: 10}
  - key: exploit_in_the_wild
    name: Exploited in the wild
    normaliser: passthrough
    default_weight: 0.3
  - key: affected_install_base
    name: Affected install base
    normaliser: logarithmic
    default_weight: 0.2
    parameters: {ceiling: 1000000}
""",
        )
        pack = FilesystemPackLoader(tmp_path).load("cybersecurity")
        assert pack.ranking_signal_keys == {
            "cvss_severity",
            "exploit_in_the_wild",
            "affected_install_base",
        }

        # And a workspace on that pack can weight them, with no engine change.
        policy = EditorialPolicy(
            scoring=ScoringProfile(weights={"cvss_severity": 0.8, "exploit_in_the_wild": 0.2})
        )
        policy.scoring.validate_against(pack.ranking_signal_keys, pack_key=pack.key)


class TestShippedPack:
    def test_ai_news_declares_its_ranking_signals(self):
        from engine.cli.container import find_project_root

        pack = FilesystemPackLoader(find_project_root() / "packs").load("ai-news")
        assert pack.ranking_signal_keys == {
            "corroboration",
            "source_authority",
            "recency",
            "prominence",
            "audience_fit",
        }

    def test_the_shipped_defaults_form_a_valid_profile(self):
        from engine.cli.container import find_project_root

        pack = FilesystemPackLoader(find_project_root() / "packs").load("ai-news")
        profile = pack.default_scoring_profile
        assert pytest.approx(sum(profile.weights.values())) == 1.0
        profile.validate_against(pack.ranking_signal_keys, pack_key=pack.key)

    def test_recency_carries_its_half_life(self):
        from engine.cli.container import find_project_root

        pack = FilesystemPackLoader(find_project_root() / "packs").load("ai-news")
        recency = pack.ranking_signal("recency")
        assert recency is not None
        assert recency.normaliser is Normaliser.DECAY
        assert recency.parameters["half_life_hours"] == 24
