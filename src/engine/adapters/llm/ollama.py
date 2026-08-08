"""Ollama-backed implementation of the LLM port -- local inference, zero cost.

Why a local model is the *development* provider rather than a toy fallback:
generation quality has to be judged before it is paid for, and a provider that
bills per token makes every iteration of a prompt a purchase decision. Running
locally means the loop between "change the brand voice" and "read the result"
costs nothing, so it can be run a hundred times.

The port's contract is honoured exactly as written, not approximately:

* **Schema conformance is the provider's job.** Ollama's ``format`` parameter
  takes a JSON Schema and constrains decoding against it (llama.cpp compiles it
  to a grammar), so tokens that would break the schema are never sampled. This
  module therefore does *not* strip code fences, hunt for the first ``{``, or
  retry on unparseable text -- that would be the retrofitted parsing the port
  explicitly rejects. If the bytes do not validate, that is an error worth
  seeing.
* **Usage is reported, not estimated.** Token counts come from the server's own
  ``prompt_eval_count`` / ``eval_count``.

Cost is always ``0.0``, and that is a fact rather than a placeholder: hardware
you already own has no per-token price. The ledger stays meaningful because
zero is the true number.

Prompt caching has no API here. Ollama reuses its KV cache across calls that
share a prefix, so ``cacheable_prefix`` is still placed first and still earns
something -- but the server reports no cache-hit counters, so
``cache_read_tokens`` stays 0 rather than being invented.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Final

import httpx
from pydantic import BaseModel, ValidationError

from engine.core.domain.ledger import TokenUsage
from engine.core.ports.llm import LLMError, LLMRequest, LLMResult, SchemaT
from engine.observability.logging import get_logger

logger = get_logger(__name__)

DEFAULT_BASE_URL: Final = "http://localhost:11434"

#: Local generation on a CPU is minutes, not seconds. The HTTP timeout has to
#: reflect the workload rather than a web-request instinct, or every honest
#: long generation looks like an outage.
DEFAULT_TIMEOUT_SECONDS: Final = 600.0

#: Ollama's own default context window is small enough to silently truncate an
#: enriched article, which is precisely how a system starts inventing facts
#: about text it never saw. Set explicitly, always.
DEFAULT_CONTEXT_TOKENS: Final = 8192

#: Low but not zero: structured fields want determinism, prose wants a little
#: room. Tuned further in the generation milestone, against real output.
DEFAULT_TEMPERATURE: Final = 0.4

#: Rough bytes-per-token for the truncation warning. Wrong in the third decimal
#: place and right about the order of magnitude, which is all it needs to be.
_CHARS_PER_TOKEN: Final = 4


class OllamaClient:
    """Structured generation against a locally running Ollama server."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        context_tokens: int = DEFAULT_CONTEXT_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._context_tokens = context_tokens
        self._temperature = temperature
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_seconds)
        # Set on the first rejection so a server or model without thinking
        # support costs one wasted call per process, not one per request.
        self._thinking_supported = True

    def generate_structured(
        self,
        request: LLMRequest,
        schema: type[SchemaT],
    ) -> LLMResult[SchemaT]:
        payload = self._payload(request, schema)
        data = self._post(payload, model=request.model)

        if not data.get("done", True) or data.get("done_reason") == "length":
            # Constrained decoding guarantees the *shape*, not that the model
            # finished. A truncated object is a different problem from a wrong
            # one -- it is fixed by raising max_tokens, not by prompting.
            raise LLMError(
                f"generation hit the {request.max_tokens}-token ceiling before finishing",
                code="max_tokens",
                model=request.model,
            )

        content = data.get("message", {}).get("content", "")
        try:
            value = schema.model_validate_json(content)
        except ValidationError as exc:
            raise LLMError(
                f"response did not satisfy {schema.__name__}: {exc}",
                code="schema_violation",
                model=request.model,
            ) from exc

        usage = TokenUsage(
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
        )
        logger.info(
            "llm.generated",
            provider="ollama",
            model=request.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            schema=schema.__name__,
        )
        return LLMResult[SchemaT](
            value=value,
            model=str(data.get("model") or request.model),
            usage=usage,
            cost_usd=0.0,
            stop_reason=data.get("done_reason"),
        )

    def estimate_cost_usd(self, usage: TokenUsage, model: str) -> float:
        """Always zero. Local inference has no per-token price to account for."""
        return 0.0

    def list_models(self) -> list[str]:
        """Model names the server has locally.

        Not part of the port -- it is inspection rather than generation, and
        exists so ``engine doctor`` can tell "the server is down" apart from
        "the model was never pulled". Those look identical at the point of use
        and call for completely different responses.

        Raises:
            LLMError: If the server cannot be reached.
        """
        try:
            response = self._client.get(f"{self._base_url}/api/tags")
            response.raise_for_status()
            body: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError(
                f"cannot reach Ollama at {self._base_url}: {exc}",
                code="provider_unreachable",
            ) from exc
        return [str(entry.get("name", "")) for entry in body.get("models", [])]

    # -- internals ---------------------------------------------------------

    def _payload(self, request: LLMRequest, schema: type[BaseModel]) -> dict[str, Any]:
        # The cacheable prefix goes first so Ollama's KV-cache reuse has a
        # stable head to match on, for the same reason a paid provider's cache
        # breakpoint does: anything volatile in front of it wastes the prefix.
        system = (
            f"{request.cacheable_prefix}\n\n{request.system}"
            if request.cacheable_prefix
            else request.system
        )
        self._warn_if_oversized(system, request.prompt, model=request.model)

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": request.prompt},
            ],
            "stream": False,
            "format": schema.model_json_schema(),
            "options": {
                "num_predict": request.max_tokens,
                "num_ctx": self._context_tokens,
                "temperature": self._temperature,
            },
        }
        if self._thinking_supported:
            # Reasoning tokens are the dominant cost on a CPU: minutes of
            # wall-clock for text the caller never sees. The schema is doing
            # the structuring work here, so the deliberation buys little.
            payload["think"] = False
        return payload

    def _warn_if_oversized(self, system: str, prompt: str, *, model: str) -> None:
        estimated = (len(system) + len(prompt)) // _CHARS_PER_TOKEN
        if estimated > self._context_tokens:
            # Ollama truncates silently. A story generated from the back half
            # of an article it never read is worse than a failed run.
            logger.warning(
                "llm.prompt_may_exceed_context",
                model=model,
                estimated_prompt_tokens=estimated,
                context_tokens=self._context_tokens,
                hint="raise ENGINE_OLLAMA_CONTEXT_TOKENS or shorten the source text",
            )

    def _post(self, payload: dict[str, Any], *, model: str) -> dict[str, Any]:
        try:
            response = self._client.post(f"{self._base_url}/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise LLMError(
                "the model did not respond in time; a larger model on CPU may need longer",
                code="timeout",
                model=model,
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(
                f"cannot reach Ollama at {self._base_url}: {exc}. Is it running?",
                code="provider_unreachable",
                model=model,
            ) from exc

        if response.status_code == 404:
            raise LLMError(
                f"model is not installed; run: ollama pull {model}",
                code="model_not_found",
                model=model,
            )
        if response.status_code >= 400:
            detail = response.text[:300]
            if self._thinking_supported and "think" in detail.lower():
                # This server or model predates thinking control. Drop the flag
                # and retry once rather than making it the operator's problem.
                logger.debug("llm.thinking_unsupported", model=model)
                self._thinking_supported = False
                payload.pop("think", None)
                return self._post(payload, model=model)
            raise LLMError(
                f"Ollama returned {response.status_code}: {detail}",
                code=f"http_{response.status_code}",
                model=model,
            )

        try:
            data: dict[str, Any] = response.json()
        except ValueError as exc:
            raise LLMError(
                "Ollama returned a non-JSON body", code="malformed_response", model=model
            ) from exc
        return data

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> OllamaClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
