"""Anthropic adapter tests, driven through a stubbed SDK client.

This provider is not on the development path -- no key is configured and no
paid call has been made. What is verified here is everything that can be
verified without spending money: that the request is *shaped* correctly, that
usage maps onto the ledger's four counters, that a cache breakpoint is placed
where it earns something, and that the two ways a successful HTTP response can
still be a failure are both handled.

Skipped entirely when the optional ``llm`` extra is absent, because the SDK
being optional is a supported state rather than a broken install.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import BaseModel

pytest.importorskip("anthropic", reason="requires the optional 'llm' extra")

from engine.adapters.llm.claude import AnthropicClient
from engine.core.domain.ledger import TokenUsage
from engine.core.ports.llm import LLMError, LLMRequest


class Answer(BaseModel):
    title: str


class StubUsage:
    def __init__(self, **counts: int) -> None:
        self.input_tokens = counts.get("input_tokens", 0)
        self.output_tokens = counts.get("output_tokens", 0)
        self.cache_read_input_tokens = counts.get("cache_read_input_tokens", 0)
        self.cache_creation_input_tokens = counts.get("cache_creation_input_tokens", 0)


class StubResponse:
    def __init__(self, *, stop_reason: str = "end_turn", **counts: int) -> None:
        self.parsed_output = Answer(title="A release")
        self.usage = StubUsage(**counts)
        self.stop_reason = stop_reason
        self.model = "claude-opus-5"
        self.id = "msg_stub"


class StubMessages:
    def __init__(self, response: StubResponse) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> StubResponse:
        self.calls.append(kwargs)
        return self._response


class StubClient:
    def __init__(self, response: StubResponse) -> None:
        self.messages = StubMessages(response)


def build_client(stub: StubClient) -> AnthropicClient:
    """The stub stands in for the SDK client, so the cast is the whole point."""
    return AnthropicClient(api_key="k", client=cast(Any, stub))


def a_request(**overrides: Any) -> LLMRequest:
    defaults = {
        "system": "You extract facts.",
        "prompt": "Text: a model was released.",
        "model": "claude-opus-5",
        "max_tokens": 1024,
    }
    return LLMRequest(**(defaults | overrides))


class TestGenerateStructured:
    def test_it_returns_the_providers_validated_instance(self):
        client = build_client(StubClient(StubResponse()))

        result = client.generate_structured(a_request(), Answer)

        assert result.value.title == "A release"
        assert result.raw_response_id == "msg_stub"

    def test_it_asks_the_provider_to_enforce_the_schema(self):
        stub = StubClient(StubResponse())
        client = build_client(stub)

        client.generate_structured(a_request(), Answer)

        assert stub.messages.calls[0]["output_format"] is Answer

    def test_usage_maps_onto_all_four_ledger_counters(self):
        stub = StubClient(
            StubResponse(
                input_tokens=100,
                output_tokens=50,
                cache_read_input_tokens=900,
                cache_creation_input_tokens=10,
            )
        )
        client = build_client(stub)

        usage = client.generate_structured(a_request(), Answer).usage

        assert usage == TokenUsage(
            input_tokens=100, output_tokens=50, cache_read_tokens=900, cache_write_tokens=10
        )

    def test_it_prices_the_call(self):
        stub = StubClient(StubResponse(input_tokens=1_000_000, output_tokens=0))
        client = build_client(stub)

        result = client.generate_structured(a_request(), Answer)

        assert result.cost_usd == pytest.approx(5.0)

    def test_a_cache_hit_is_cheaper_than_the_same_tokens_fresh(self):
        """Proves the cacheable prefix is worth carrying through the port."""
        client = build_client(StubClient(StubResponse()))

        fresh = client.estimate_cost_usd(TokenUsage(input_tokens=500_000), "claude-opus-5")
        cached = client.estimate_cost_usd(TokenUsage(cache_read_tokens=500_000), "claude-opus-5")

        assert cached < fresh


class TestCaching:
    def test_without_a_prefix_the_system_prompt_is_one_plain_block(self):
        stub = StubClient(StubResponse())
        client = build_client(stub)

        client.generate_structured(a_request(), Answer)

        system = stub.messages.calls[0]["system"]
        assert system == [{"type": "text", "text": "You extract facts."}]

    def test_the_prefix_leads_and_carries_the_breakpoint(self):
        stub = StubClient(StubResponse())
        client = build_client(stub)

        client.generate_structured(a_request(cacheable_prefix="BRAND VOICE"), Answer)

        system = stub.messages.calls[0]["system"]
        assert system[0]["text"] == "BRAND VOICE"
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in system[1]


class TestFailures:
    def test_a_refusal_is_reported_rather_than_read_as_output(self):
        """A declined request is a successful HTTP response, not an exception."""
        stub = StubClient(StubResponse(stop_reason="refusal"))
        client = build_client(stub)

        with pytest.raises(LLMError) as caught:
            client.generate_structured(a_request(), Answer)

        assert caught.value.code == "refusal"

    def test_truncation_is_reported_as_its_own_failure(self):
        stub = StubClient(StubResponse(stop_reason="max_tokens"))
        client = build_client(stub)

        with pytest.raises(LLMError) as caught:
            client.generate_structured(a_request(), Answer)

        assert caught.value.code == "max_tokens"

    def test_an_unpriceable_model_refuses_before_the_ledger_sees_it(self):
        client = build_client(StubClient(StubResponse()))

        with pytest.raises(LLMError) as caught:
            client.generate_structured(a_request(model="claude-made-up-9"), Answer)

        assert caught.value.code == "unknown_model_price"
