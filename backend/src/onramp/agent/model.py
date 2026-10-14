"""A provider-neutral interface to a tool-calling chat model.

The investigator talks to models only through this interface, so a provider can be swapped
and tests can run the real graph with a scripted model.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolOutput:
    call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool"]
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_outputs: tuple[ToolOutput, ...] = ()
    # The provider's own content for an assistant turn, replayed unchanged so blocks the
    # provider requires back (thinking, fallback markers) are preserved.
    raw: Any = None


@dataclass(frozen=True)
class Usage:
    # Every input token, cached or not: what the investigation budget counts.
    input_tokens: int = 0
    output_tokens: int = 0
    # The parts of input_tokens read from or written to the prompt cache; they are priced
    # differently from the rest.
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class ModelResponse:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=Usage)
    raw: Any = None


class ChatModel(Protocol):
    @property
    def name(self) -> str: ...

    def respond(
        self, system: str, transcript: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelResponse: ...


class ModelUnavailableError(Exception):
    """The provider failed or timed out. The investigation may be retried later."""


class ModelRefusedError(Exception):
    """The provider declined the request. Retrying the same input will not help."""


Script = Callable[[Sequence[Message]], ModelResponse]


class ScriptedModel:
    """Replays scripted responses in order, or computes them from the transcript.

    Used to run the real investigation graph deterministically in tests.
    """

    def __init__(self, steps: Sequence[ModelResponse | Script], name: str = "scripted") -> None:
        self._steps = list(steps)
        self._name = name
        self.calls: list[tuple[str, tuple[Message, ...]]] = []

    @property
    def name(self) -> str:
        return self._name

    def respond(
        self, system: str, transcript: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelResponse:
        self.calls.append((system, tuple(transcript)))
        if not self._steps:
            raise ModelUnavailableError("script exhausted")
        step = self._steps.pop(0)
        return step(transcript) if callable(step) else step


def call(name: str, call_id: str | None = None, **arguments: Any) -> ToolCall:
    return ToolCall(id=call_id or f"call_{name}", name=name, arguments=arguments)


def tool_turn(*calls: ToolCall, tokens: int = 100) -> ModelResponse:
    return ModelResponse(tool_calls=calls, usage=Usage(tokens, tokens // 4))
