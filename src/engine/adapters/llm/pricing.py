"""Token prices, as data rather than arithmetic scattered through the adapters.

Prices change more often than code does, and a rate inlined at the call site is
a rate nobody updates. Keeping the table here means repricing is a one-line
diff with a test that fails loudly when a model disappears.

Two deliberate choices:

* **An unknown model raises.** Returning ``0.0`` for a model that is not in the
  table would silently under-report spend against the per-run ceiling, which is
  the one number that exists specifically to stop a runaway loop. A typo in a
  model name must fail immediately, not produce a free-looking invoice.
* **List price, never promotional price.** Introductory rates expire, and a
  ledger that quietly assumes a discount under-reports the moment it lapses.
  Over-estimating is the safe direction for a ceiling; under-estimating is not.

Local inference is priced nowhere in this module. A model running on your own
machine has no per-token price to look up -- see
:class:`~engine.adapters.llm.ollama.OllamaClient`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from engine.core.domain.ledger import TokenUsage
from engine.core.ports.llm import LLMError

_PER_MILLION: Final = 1_000_000


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """What one model charges, per million tokens."""

    input_usd: float
    output_usd: float
    #: Prompt-cache hits bill at ~10% of the input rate. This is the number the
    #: whole ``cacheable_prefix`` design exists to earn.
    cache_read_multiplier: float = 0.1
    #: Writing the cache costs a premium over plain input (5-minute TTL). Two
    #: reads pay it back; one does not.
    cache_write_multiplier: float = 1.25

    def cost_usd(self, usage: TokenUsage) -> float:
        """Price a usage record."""
        input_rate = self.input_usd / _PER_MILLION
        return (
            usage.input_tokens * input_rate
            + usage.output_tokens * (self.output_usd / _PER_MILLION)
            + usage.cache_read_tokens * input_rate * self.cache_read_multiplier
            + usage.cache_write_tokens * input_rate * self.cache_write_multiplier
        )


#: Anthropic list prices, USD per million tokens. Verified 2026-08-07.
ANTHROPIC_PRICES: Final[dict[str, ModelPrice]] = {
    "claude-fable-5": ModelPrice(input_usd=10.0, output_usd=50.0),
    "claude-mythos-5": ModelPrice(input_usd=10.0, output_usd=50.0),
    "claude-opus-5": ModelPrice(input_usd=5.0, output_usd=25.0),
    "claude-opus-4-8": ModelPrice(input_usd=5.0, output_usd=25.0),
    "claude-opus-4-7": ModelPrice(input_usd=5.0, output_usd=25.0),
    "claude-opus-4-6": ModelPrice(input_usd=5.0, output_usd=25.0),
    # List price. An introductory rate is in effect at time of writing; see the
    # module docstring for why the ledger ignores it.
    "claude-sonnet-5": ModelPrice(input_usd=3.0, output_usd=15.0),
    "claude-sonnet-4-6": ModelPrice(input_usd=3.0, output_usd=15.0),
    "claude-haiku-4-5": ModelPrice(input_usd=1.0, output_usd=5.0),
}

#: Inference on hardware you already own. Priced at zero because there is no
#: per-token charge to account for -- electricity is not a token cost, and
#: pretending otherwise would put a fictional number in the ledger.
FREE: Final = ModelPrice(input_usd=0.0, output_usd=0.0)


def anthropic_price(model: str) -> ModelPrice:
    """Look up a model's price, or refuse to guess.

    Raises:
        LLMError: If the model is not in the table.
    """
    price = ANTHROPIC_PRICES.get(model)
    if price is None:
        known = ", ".join(sorted(ANTHROPIC_PRICES))
        raise LLMError(
            f"no price on record; add it to ANTHROPIC_PRICES (known: {known})",
            code="unknown_model_price",
            model=model,
        )
    return price
