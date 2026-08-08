"""Ollama adapter tests, driven through a fake transport.

The real model is exercised separately, by hand, against the corpus -- these
cover the parts a live call cannot demonstrate on demand: what happens when the
server rejects a flag, truncates an answer, or is not running at all.

The recurring theme is that the adapter must never *repair* a bad response. The
port requires schema conformance from the provider; code here that salvaged
malformed output would hide exactly the failure the operator needs to see.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from engine.adapters.llm.ollama import OllamaClient
from engine.core.ports.llm import LLMError, LLMRequest


class Answer(BaseModel):
    title: str
    points: list[str]


def ok_body(content: str, **overrides: Any) -> dict[str, Any]:
    body = {
        "model": "qwen3:4b",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 120,
        "eval_count": 45,
    }
    return body | overrides


def build(
    responses: list[httpx.Response] | httpx.Response,
    captured: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> OllamaClient:
    queue = responses if isinstance(responses, list) else [responses]
    remaining = list(queue)

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(json.loads(request.content))
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    transport = httpx.MockTransport(handler)
    return OllamaClient(client=httpx.Client(transport=transport), **kwargs)


def a_request(**overrides: Any) -> LLMRequest:
    defaults = {
        "system": "You extract facts.",
        "prompt": "Text: a model was released.",
        "model": "qwen3:4b",
        "max_tokens": 512,
    }
    return LLMRequest(**(defaults | overrides))


class TestGenerateStructured:
    def test_it_returns_a_validated_instance(self):
        payload = json.dumps({"title": "A release", "points": ["open weights"]})
        client = build(httpx.Response(200, json=ok_body(payload)))

        result = client.generate_structured(a_request(), Answer)

        assert result.value.title == "A release"
        assert result.value.points == ["open weights"]

    def test_it_reports_the_servers_own_token_counts(self):
        """Estimated tokens would make the ledger fiction."""
        payload = json.dumps({"title": "x", "points": []})
        client = build(httpx.Response(200, json=ok_body(payload)))

        result = client.generate_structured(a_request(), Answer)

        assert result.usage.input_tokens == 120
        assert result.usage.output_tokens == 45

    def test_local_generation_is_free(self):
        payload = json.dumps({"title": "x", "points": []})
        client = build(httpx.Response(200, json=ok_body(payload)))

        result = client.generate_structured(a_request(), Answer)

        assert result.cost_usd == 0.0
        assert client.estimate_cost_usd(result.usage, "qwen3:4b") == 0.0

    def test_it_sends_the_schema_so_the_server_constrains_decoding(self):
        """Schema conformance is the provider's job, not a post-hoc parse."""
        captured: list[dict[str, Any]] = []
        payload = json.dumps({"title": "x", "points": []})
        client = build(httpx.Response(200, json=ok_body(payload)), captured)

        client.generate_structured(a_request(), Answer)

        assert captured[0]["format"] == Answer.model_json_schema()
        assert captured[0]["stream"] is False

    def test_it_sets_the_context_window_explicitly(self):
        """The default silently truncates an article; that must never be relied on."""
        captured: list[dict[str, Any]] = []
        payload = json.dumps({"title": "x", "points": []})
        client = build(httpx.Response(200, json=ok_body(payload)), captured, context_tokens=16384)

        client.generate_structured(a_request(), Answer)

        assert captured[0]["options"]["num_ctx"] == 16384
        assert captured[0]["options"]["num_predict"] == 512

    def test_the_cacheable_prefix_leads_the_system_message(self):
        """Prefix caching is a prefix match: anything volatile in front wastes it."""
        captured: list[dict[str, Any]] = []
        payload = json.dumps({"title": "x", "points": []})
        client = build(httpx.Response(200, json=ok_body(payload)), captured)

        client.generate_structured(a_request(cacheable_prefix="BRAND VOICE RULES"), Answer)

        system = captured[0]["messages"][0]["content"]
        assert system.startswith("BRAND VOICE RULES")
        assert system.endswith("You extract facts.")


class TestFailures:
    def test_output_that_violates_the_schema_is_an_error_not_a_repair(self):
        client = build(httpx.Response(200, json=ok_body('{"title": "x"}')))

        with pytest.raises(LLMError) as caught:
            client.generate_structured(a_request(), Answer)

        assert caught.value.code == "schema_violation"

    def test_prose_around_json_is_not_salvaged(self):
        """Fence-stripping here would mask a misconfigured server."""
        client = build(httpx.Response(200, json=ok_body('```json\n{"title":"x","points":[]}\n```')))

        with pytest.raises(LLMError) as caught:
            client.generate_structured(a_request(), Answer)

        assert caught.value.code == "schema_violation"

    def test_truncation_is_reported_as_its_own_failure(self):
        """Fixed by raising max_tokens, not by changing the prompt."""
        client = build(httpx.Response(200, json=ok_body('{"title": "x"', done_reason="length")))

        with pytest.raises(LLMError) as caught:
            client.generate_structured(a_request(), Answer)

        assert caught.value.code == "max_tokens"

    def test_a_missing_model_says_how_to_install_it(self):
        client = build(httpx.Response(404, json={"error": "model not found"}))

        with pytest.raises(LLMError) as caught:
            client.generate_structured(a_request(), Answer)

        assert caught.value.code == "model_not_found"
        assert "ollama pull qwen3:4b" in str(caught.value)

    def test_a_stopped_server_says_so(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = OllamaClient(client=httpx.Client(transport=httpx.MockTransport(handler)))

        with pytest.raises(LLMError) as caught:
            client.generate_structured(a_request(), Answer)

        assert caught.value.code == "provider_unreachable"

    def test_a_timeout_is_distinguished_from_an_outage(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow")

        client = OllamaClient(client=httpx.Client(transport=httpx.MockTransport(handler)))

        with pytest.raises(LLMError) as caught:
            client.generate_structured(a_request(), Answer)

        assert caught.value.code == "timeout"


class TestListModels:
    """Backs ``engine doctor``, which must tell two failures apart."""

    def test_it_reports_what_the_server_has(self):
        client = build(
            httpx.Response(200, json={"models": [{"name": "qwen3:4b"}, {"name": "gemma3:1b"}]})
        )

        assert client.list_models() == ["qwen3:4b", "gemma3:1b"]

    def test_an_empty_server_is_not_an_error(self):
        """Nothing pulled yet is a normal state, distinct from being down."""
        client = build(httpx.Response(200, json={"models": []}))

        assert client.list_models() == []

    def test_an_unreachable_server_is_reported_as_such(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = OllamaClient(client=httpx.Client(transport=httpx.MockTransport(handler)))

        with pytest.raises(LLMError) as caught:
            client.list_models()

        assert caught.value.code == "provider_unreachable"


class TestThinkingCompatibility:
    def test_a_server_that_rejects_the_thinking_flag_is_retried_without_it(self):
        """Older servers and non-reasoning models must not need operator action."""
        captured: list[dict[str, Any]] = []
        payload = json.dumps({"title": "x", "points": []})
        client = build(
            [
                httpx.Response(400, text='{"error":"model does not support think"}'),
                httpx.Response(200, json=ok_body(payload)),
            ],
            captured,
        )

        result = client.generate_structured(a_request(), Answer)

        assert result.value.title == "x"
        assert "think" in captured[0]
        assert "think" not in captured[1]

    def test_the_flag_stays_off_for_later_calls(self):
        """One wasted call per process, not one per request."""
        captured: list[dict[str, Any]] = []
        payload = json.dumps({"title": "x", "points": []})
        client = build(
            [
                httpx.Response(400, text='{"error":"think is not supported"}'),
                httpx.Response(200, json=ok_body(payload)),
            ],
            captured,
        )

        client.generate_structured(a_request(), Answer)
        client.generate_structured(a_request(), Answer)

        assert all("think" not in sent for sent in captured[1:])
