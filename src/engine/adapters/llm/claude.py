"""Anthropic-backed implementation of the LLM port.

Not on the development path. It is written, tested and wired now so that
switching providers later is a configuration change (``ENGINE_LLM_PROVIDER``)
rather than a project -- but nothing in the engine imports it, and running
without an API key or even without the SDK installed is a supported state.

The SDK import is deliberately deferred to construction. ``anthropic`` lives
behind the ``llm`` extra, and a module-level import would make an optional
dependency mandatory for anyone who merely starts the CLI.

Provider details worth knowing when reading this:

* **Structured output is a first-class request parameter.** ``messages.parse``
  takes the Pydantic model and returns a validated instance, so schema
  conformance is enforced by the provider exactly as the port requires.
* **Caching is explicit.** ``cacheable_prefix`` becomes a system block carrying
  a cache breakpoint. Cache reads bill at ~10% of input, which is why the port
  carries the field at all. Below the provider's minimum cacheable length the
  breakpoint is silently ignored -- no error, just no saving.
* **Sampling parameters are rejected.** Current models return 400 for
  ``temperature``/``top_p``, so behaviour is steered by the prompt.
* **A refusal is a successful HTTP response.** The safety classifiers can
  decline, and reading the parsed output without checking ``stop_reason``
  first would surface it as a confusing schema error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from engine.adapters.llm.pricing import anthropic_price
from engine.core.domain.ledger import TokenUsage
from engine.core.ports.llm import LLMError, LLMRequest, LLMResult, SchemaT
from engine.observability.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover -- typing only, never imported at runtime
    from anthropic import Anthropic
    from anthropic.types import TextBlockParam

logger = get_logger(__name__)


class AnthropicClient:
    """Structured generation against the Anthropic Messages API."""

    def __init__(self, *, api_key: str, client: Anthropic | None = None) -> None:
        if client is None:
            try:
                from anthropic import Anthropic as _Anthropic
            except ImportError as exc:  # pragma: no cover -- exercised by hand
                raise LLMError(
                    "the anthropic SDK is not installed; run: pip install -e '.[llm]'",
                    code="provider_unavailable",
                ) from exc
            client = _Anthropic(api_key=api_key)
        self._client = client

    def generate_structured(
        self,
        request: LLMRequest,
        schema: type[SchemaT],
    ) -> LLMResult[SchemaT]:
        import anthropic

        try:
            response = self._client.messages.parse(
                model=request.model,
                max_tokens=request.max_tokens,
                system=self._system_blocks(request),
                messages=[{"role": "user", "content": request.prompt}],
                output_format=schema,
            )
        except anthropic.APIStatusError as exc:
            raise LLMError(
                f"provider returned {exc.status_code}: {exc.message}",
                code=f"http_{exc.status_code}",
                model=request.model,
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(
                f"could not reach the provider: {exc}",
                code="provider_unreachable",
                model=request.model,
            ) from exc

        if response.stop_reason == "refusal":
            raise LLMError(
                "the provider's safety classifiers declined this request",
                code="refusal",
                model=request.model,
            )
        if response.stop_reason == "max_tokens":
            raise LLMError(
                f"generation hit the {request.max_tokens}-token ceiling before finishing",
                code="max_tokens",
                model=request.model,
            )

        value: SchemaT | None = response.parsed_output
        if value is None:
            # Structured output is a request parameter, so this should not
            # happen -- but the SDK types it as optional, and silently
            # returning an empty object would be worse than saying so.
            raise LLMError(
                f"provider returned no output for {schema.__name__}",
                code="empty_response",
                model=request.model,
            )
        usage = self._usage(response.usage)
        cost = self.estimate_cost_usd(usage, request.model)
        logger.info(
            "llm.generated",
            provider="anthropic",
            model=request.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cost_usd=round(cost, 6),
            schema=schema.__name__,
        )
        return LLMResult[SchemaT](
            value=value,
            model=response.model,
            usage=usage,
            cost_usd=cost,
            stop_reason=response.stop_reason,
            raw_response_id=response.id,
        )

    def estimate_cost_usd(self, usage: TokenUsage, model: str) -> float:
        return anthropic_price(model).cost_usd(usage)

    # -- internals ---------------------------------------------------------

    def _system_blocks(self, request: LLMRequest) -> list[TextBlockParam]:
        """System prompt, with the cache breakpoint after the stable part.

        Order matters more than the marker does: caching is a prefix match, so
        anything that varies per request placed ahead of the breakpoint
        invalidates everything behind it.

        The default 5-minute entry lifetime is what the field is sized for: one
        pipeline run reusing a single brand profile across a batch of stories.
        """
        if not request.cacheable_prefix:
            return [{"type": "text", "text": request.system}]
        return [
            {
                "type": "text",
                "text": request.cacheable_prefix,
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": request.system},
        ]

    def _usage(self, usage: Any) -> TokenUsage:
        return TokenUsage(
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )
