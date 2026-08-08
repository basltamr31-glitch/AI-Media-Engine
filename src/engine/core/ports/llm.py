"""Language-model port.

No provider SDK type may escape ``engine.adapters.llm``. Models, prices and
capabilities change every few months; the business logic must not.

The port is deliberately narrow -- structured generation with usage accounting --
because a wide, provider-shaped interface would leak vendor semantics back into
the domain and undo the abstraction.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, Field

from engine.core.domain.base import DomainModel
from engine.core.domain.ledger import TokenUsage

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised when a generation could not be completed or could not be trusted.

    A single exception type, like :class:`~engine.core.ports.enrichment.ExtractionError`:
    callers isolate the failure per item rather than branching on the cause, and
    the stable ``code`` is what gets counted. Deriving a category by slicing the
    message is how one provider's phrasing becomes a schema change.
    """

    def __init__(self, message: str, *, code: str = "llm_failed", model: str = "") -> None:
        self.code = code
        self.model = model
        super().__init__(f"{message} (model={model})" if model else message)


class LLMRequest(DomainModel):
    """A single structured-generation request."""

    system: str = Field(description="Stable instruction prefix. Cached across calls.")
    prompt: str = Field(description="Per-request content. Placed after the cache boundary.")
    model: str
    max_tokens: int = Field(default=8192, gt=0)
    cacheable_prefix: str | None = Field(
        default=None,
        description=(
            "Large stable context (brand profile, editorial rules) eligible for "
            "prompt caching. Cache reads cost ~10% of input, so this is a "
            "primary gross-margin lever."
        ),
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMResult[T: BaseModel](DomainModel):
    """A validated structured response plus what it cost.

    Usage travels with the result rather than being logged separately so that
    cost attribution per story, per channel and per workspace is always
    possible -- the input to any future pricing decision.
    """

    value: T
    model: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost_usd: float = Field(default=0.0, ge=0.0)
    stop_reason: str | None = None
    raw_response_id: str | None = None


@runtime_checkable
class LLMPort(Protocol):
    """Provider-agnostic structured generation."""

    def generate_structured(
        self,
        request: LLMRequest,
        schema: type[SchemaT],
    ) -> LLMResult[SchemaT]:
        """Generate a response conforming to ``schema``.

        Implementations must guarantee the returned value validates against the
        schema. Parsing free-form text out of a chat response is not an
        acceptable implementation -- schema conformance is a provider feature we
        require, not something we retrofit.
        """
        ...

    def estimate_cost_usd(self, usage: TokenUsage, model: str) -> float:
        """Price a usage record for the run ledger."""
        ...
