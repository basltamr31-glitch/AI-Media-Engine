"""Language-model adapters.

The boundary the port promises: no provider SDK type escapes this package.
Everything above it sees :class:`~engine.core.ports.llm.LLMRequest` and
:class:`~engine.core.ports.llm.LLMResult`, so which model wrote the text is a
configuration question rather than an architectural one.

Two providers ship today. :class:`~engine.adapters.llm.ollama.OllamaClient`
runs locally and costs nothing, which is what makes it possible to judge
generation quality before paying for it;
:class:`~engine.adapters.llm.claude.AnthropicClient` is the same interface
against a hosted frontier model, selected by configuration when that judgement
says it is worth the money.
"""

from engine.adapters.llm.claude import AnthropicClient
from engine.adapters.llm.ollama import OllamaClient
from engine.adapters.llm.pricing import ANTHROPIC_PRICES, FREE, ModelPrice, anthropic_price

__all__ = [
    "ANTHROPIC_PRICES",
    "FREE",
    "AnthropicClient",
    "ModelPrice",
    "OllamaClient",
    "anthropic_price",
]
