"""The investigator's Anthropic provider. Requires the ``anthropic`` extra and credentials.

* Assistant turns are replayed exactly as returned, so thinking and fallback blocks survive.
* Tool and system definitions are a stable prefix; automatic prompt caching reuses it on
  every turn of the investigation.
* Server-side refusal fallback is on (``fallbacks: "default"``) for models that support it. A
  refusal that survives it, or any refusal on other models, ends the investigation and the case
  escalates.
* Transient provider failures surface as ModelUnavailableError after the SDK's own retries;
  configuration errors (bad request, authentication) are raised unchanged.
"""

from collections.abc import Sequence
from typing import Any

from onramp.agent.model import (
    Message,
    ModelRefusedError,
    ModelResponse,
    ModelUnavailableError,
    ToolCall,
    ToolDefinition,
    Usage,
)

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept the server-side refusal fallback. Haiku has none; sending it would fail
# every call, so those models are called without it and a refusal escalates.
FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-sonnet-5-5"})


class AnthropicChatModel:
    def __init__(
        self,
        client: Any | None = None,
        *,
        model: str = DEFAULT_MODEL,
        effort: str = "medium",
        max_tokens: int = 16_000,
        timeout_s: float = 60.0,
    ) -> None:
        if client is None:
            import anthropic  # optional dependency

            client = anthropic.Anthropic(timeout=timeout_s)
        # Typed loosely on purpose: messages are built as plain dicts by to_wire below.
        self._client: Any = client
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return f"{self._model}@{self._effort}"

    def respond(
        self, system: str, transcript: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelResponse:
        import anthropic

        params = self.request(system, transcript, tools)
        try:
            if self._model in FALLBACK_MODELS:
                response = self._client.beta.messages.create(
                    **params, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = self._client.messages.create(**params)
        except (
            anthropic.APIConnectionError,  # includes APITimeoutError
            anthropic.RateLimitError,
            anthropic.InternalServerError,
        ) as exc:
            raise ModelUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        return from_wire(response)

    def request(
        self, system: str, transcript: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> dict[str, Any]:
        """The Messages API parameters for one turn, shared by live and batched calls."""
        return {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "output_config": {"effort": self._effort},
            "cache_control": {"type": "ephemeral"},
            "system": system,
            "tools": [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ],
            "messages": [to_wire(m) for m in transcript],
        }


def to_wire(message: Message) -> dict[str, Any]:
    if message.role == "assistant":
        if message.raw is not None:
            return {"role": "assistant", "content": message.raw}
        content: list[dict[str, Any]] = []
        if message.text:
            content.append({"type": "text", "text": message.text})
        content += [
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
            for c in message.tool_calls
        ]
        return {"role": "assistant", "content": content}
    if message.role == "tool":
        # All results for one turn go back in a single user message.
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": o.call_id,
                    "content": o.content,
                    "is_error": o.is_error,
                }
                for o in message.tool_outputs
            ],
        }
    return {"role": "user", "content": message.text}


def from_wire(response: Any) -> ModelResponse:
    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise ModelRefusedError(f"model declined (category: {category})")
    text = "".join(b.text for b in response.content if b.type == "text")
    calls = tuple(
        ToolCall(id=b.id, name=b.name, arguments=dict(b.input))
        for b in response.content
        if b.type == "tool_use"
    )
    usage = response.usage
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return ModelResponse(
        text=text,
        tool_calls=calls,
        usage=Usage(
            input_tokens=(usage.input_tokens or 0) + cache_read + cache_write,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        ),
        raw=response.content,
    )
