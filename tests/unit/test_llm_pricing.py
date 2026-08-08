"""Price-table tests.

The ledger's numbers are only as good as this table, and a silently wrong rate
is worse than a missing one: it produces a plausible invoice estimate that is
never questioned. These tests pin the arithmetic and the refusal to guess.
"""

from __future__ import annotations

import pytest

from engine.adapters.llm.pricing import ANTHROPIC_PRICES, FREE, ModelPrice, anthropic_price
from engine.core.domain.ledger import TokenUsage
from engine.core.ports.llm import LLMError


class TestModelPrice:
    def test_it_prices_plain_input_and_output(self):
        price = ModelPrice(input_usd=5.0, output_usd=25.0)
        usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)

        assert price.cost_usd(usage) == pytest.approx(30.0)

    def test_a_cache_hit_costs_a_tenth_of_fresh_input(self):
        """The entire reason LLMRequest carries a cacheable prefix."""
        price = ModelPrice(input_usd=5.0, output_usd=25.0)

        fresh = price.cost_usd(TokenUsage(input_tokens=1_000_000))
        cached = price.cost_usd(TokenUsage(cache_read_tokens=1_000_000))

        assert cached == pytest.approx(fresh * 0.1)

    def test_writing_the_cache_costs_more_than_not_caching(self):
        """One call does not pay the premium back; the second one does."""
        price = ModelPrice(input_usd=5.0, output_usd=25.0)
        tokens = 1_000_000

        uncached_twice = 2 * price.cost_usd(TokenUsage(input_tokens=tokens))
        write_then_read = price.cost_usd(TokenUsage(cache_write_tokens=tokens)) + price.cost_usd(
            TokenUsage(cache_read_tokens=tokens)
        )

        assert price.cost_usd(TokenUsage(cache_write_tokens=tokens)) > price.cost_usd(
            TokenUsage(input_tokens=tokens)
        )
        assert write_then_read < uncached_twice

    def test_local_inference_is_free_at_any_volume(self):
        assert FREE.cost_usd(TokenUsage(input_tokens=10**9, output_tokens=10**9)) == 0.0

    def test_no_usage_costs_nothing(self):
        assert ModelPrice(input_usd=5.0, output_usd=25.0).cost_usd(TokenUsage()) == 0.0


class TestLookup:
    def test_it_prices_a_known_model(self):
        assert anthropic_price("claude-opus-5").input_usd == 5.0

    def test_an_unknown_model_refuses_rather_than_returning_zero(self):
        """A typo must not read as a free model against the spend ceiling."""
        with pytest.raises(LLMError) as caught:
            anthropic_price("claude-opus-6-turbo")

        assert caught.value.code == "unknown_model_price"
        assert "claude-opus-6-turbo" in str(caught.value)

    def test_every_configured_default_model_has_a_price(self):
        """A default nobody can price is a ceiling nobody can enforce."""
        from engine.settings import Settings

        defaults = Settings.model_fields
        for field in ("generation_model", "extraction_model"):
            assert defaults[field].default in ANTHROPIC_PRICES
