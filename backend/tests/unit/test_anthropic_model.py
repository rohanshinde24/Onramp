"""The Anthropic adapter against a fake client: request shape and error mapping, no network."""

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from onramp.agent.anthropic_model import AnthropicChatModel, from_wire, to_wire
from onramp.agent.model import (
    Message,
    ModelRefusedError,
    ModelUnavailableError,
    ToolCall,
    ToolDefinition,
    ToolOutput,
)


def _response(*blocks: Any, stop_reason: str = "tool_use") -> SimpleNamespace:
    return SimpleNamespace(
        content=list(blocks),
        stop_reason=stop_reason,
        stop_details=None,
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=40,
            cache_read_input_tokens=900,
            cache_creation_input_tokens=0,
        ),
    )


class FakeClient:
    def __init__(self, result: Any) -> None:
        self.requests: list[dict[str, Any]] = []
        self._result = result
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


TOOL = ToolDefinition("get_case", "The case.", {"type": "object", "properties": {}})
USE = SimpleNamespace(type="tool_use", id="tu_1", name="get_case", input={})


def test_request_enables_fallback_caching_and_explicit_effort() -> None:
    client = FakeClient(_response(USE))
    model = AnthropicChatModel(client, effort="high")

    model.respond("system prompt", [Message("user", text="go")], [TOOL])

    (request,) = client.requests
    assert request["model"] == "claude-opus-5-5"
    assert request["fallbacks"] == "default"
    assert request["betas"] == ["server-side-fallback-2026-07-01"]
    assert request["cache_control"] == {"type": "ephemeral"}
    assert request["output_config"] == {"effort": "high"}
    assert request["tools"] == [
        {
            "name": "get_case",
            "description": "The case.",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    assert "tool_choice" not in request  # forced tool use is rejected on this model


def test_tool_calls_usage_and_raw_content_are_returned() -> None:
    response = from_wire(_response(SimpleNamespace(type="text", text="Looking."), USE))

    assert response.tool_calls == (ToolCall("tu_1", "get_case", {}),)
    assert response.text == "Looking."
    assert response.usage.input_tokens == 1000  # cached input counts toward the budget
    assert (response.usage.cache_read_tokens, response.usage.cache_write_tokens) == (900, 0)
    assert response.raw is not None


def test_assistant_turn_replays_provider_content_unchanged() -> None:
    raw = [SimpleNamespace(type="thinking", thinking="", signature="sig"), USE]

    wire = to_wire(Message("assistant", raw=raw))

    assert wire == {"role": "assistant", "content": raw}


def test_tool_results_go_back_together_with_error_flags() -> None:
    wire = to_wire(
        Message(
            "tool",
            tool_outputs=(
                ToolOutput("tu_1", '{"ok": 1}'),
                ToolOutput("tu_2", '{"error": "x"}', is_error=True),
            ),
        )
    )

    assert wire["role"] == "user"
    assert [b["is_error"] for b in wire["content"]] == [False, True]
    assert {b["type"] for b in wire["content"]} == {"tool_result"}


def test_refusal_is_raised_not_read_as_content() -> None:
    with pytest.raises(ModelRefusedError):
        from_wire(_response(stop_reason="refusal"))


def _request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize(
    "error",
    [
        anthropic.APIConnectionError(request=_request()),
        anthropic.APITimeoutError(request=_request()),
        anthropic.RateLimitError(
            "slow down", response=httpx2.Response(429, request=_request()), body=None
        ),
        anthropic.InternalServerError(
            "oops", response=httpx2.Response(500, request=_request()), body=None
        ),
    ],
)
def test_transient_failures_become_model_unavailable(error: Exception) -> None:
    with pytest.raises(ModelUnavailableError):
        AnthropicChatModel(FakeClient(error)).respond("s", [Message("user", text="x")], [TOOL])


def test_configuration_errors_are_not_hidden() -> None:
    error = anthropic.AuthenticationError(
        "bad key", response=httpx2.Response(401, request=_request()), body=None
    )

    with pytest.raises(anthropic.AuthenticationError):
        AnthropicChatModel(FakeClient(error)).respond("s", [Message("user", text="x")], [TOOL])


def test_models_without_a_refusal_fallback_are_called_without_it() -> None:
    client = FakeClient(_response(USE))
    model = AnthropicChatModel(client, model="claude-haiku-5-5")

    model.respond("system prompt", [Message("user", text="go")], [TOOL])

    (request,) = client.requests
    assert request["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in request
    assert "betas" not in request
