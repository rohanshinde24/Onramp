"""Recorded model answers, so an evaluation never pays twice for the same answer.

Every live answer is saved with a digest of exactly what the model was shown for that turn:
the system prompt, the tool definitions and the whole transcript so far. On a later run the
recorded answer is replayed only while each turn's digest still matches. The first turn whose
input differs (because a world, a prompt, a tool or the code changed) and every turn after it
are asked live again and re-recorded. A recording that ends early, after a crash or a spending
cap, resumes live from where it stopped.

Replayed answers cost nothing now. The run still reports what they cost when they were made,
so cost per correct outcome stays comparable between fresh and replayed runs.
"""

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from onramp.agent.model import ChatModel, Message, ModelResponse, ToolCall, ToolDefinition, Usage
from onramp.evaluation.spend import cost, price_for

RECORDING_SCHEMA_VERSION = 1


class RecordedTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    digest: str
    text: str
    tool_calls: tuple[tuple[str, str, dict[str, Any]], ...]
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: Decimal
    batch: bool
    raw: list[dict[str, Any]] | None

    def response(self) -> ModelResponse:
        return ModelResponse(
            text=self.text,
            tool_calls=tuple(ToolCall(i, n, dict(a)) for i, n, a in self.tool_calls),
            usage=Usage(
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                cache_read_tokens=self.cache_read_tokens,
                cache_write_tokens=self.cache_write_tokens,
            ),
            raw=self.raw,
        )


class Recording(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = RECORDING_SCHEMA_VERSION
    model: str
    prompt_version: str
    scenario_id: str
    repeat: int
    turns: list[RecordedTurn] = []


@dataclass(frozen=True)
class RecordingKey:
    model: str
    prompt_version: str
    scenario_id: str
    repeat: int = 0


class RecordingStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def path(self, key: RecordingKey) -> Path:
        model = key.model.replace("/", "_").replace("@", "_at_")
        suffix = f"-r{key.repeat}" if key.repeat else ""
        return self._root / model / key.prompt_version / f"{key.scenario_id}{suffix}.json"

    def load(self, key: RecordingKey) -> Recording:
        path = self.path(key)
        if not path.exists():
            return Recording(
                model=key.model,
                prompt_version=key.prompt_version,
                scenario_id=key.scenario_id,
                repeat=key.repeat,
            )
        return Recording.model_validate_json(path.read_text())

    def save(self, key: RecordingKey, recording: Recording) -> None:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(recording.model_dump_json(indent=1))
        tmp.replace(path)  # never leave a half-written recording


def request_digest(
    system: str, transcript: Sequence[Message], tools: Sequence[ToolDefinition]
) -> str:
    """A stable fingerprint of everything the model is shown for one turn."""
    payload = {
        "system": system,
        "tools": [[t.name, t.description, t.input_schema] for t in tools],
        "transcript": [
            {
                "role": m.role,
                "text": m.text,
                "calls": [[c.id, c.name, c.arguments] for c in m.tool_calls],
                "outputs": [[o.call_id, o.content, o.is_error] for o in m.tool_outputs],
            }
            for m in transcript
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass
class RecordedModel:
    """Replays matching recorded turns and asks the live model only for the rest.

    ``live`` is called lazily, so a fully replayed run never builds a provider client.
    """

    key: RecordingKey
    store: RecordingStore
    live: Callable[[], ChatModel]
    batch: bool = False
    replayed_turns: int = 0
    live_turns: int = 0
    _recording: Recording | None = None
    _live_model: ChatModel | None = None
    # Cost of each answer when it was made; read by the meter for replayed turns.
    turn_costs: list[Decimal] = field(default_factory=list)
    last_replayed: bool = False

    @property
    def name(self) -> str:
        return self.key.model

    def respond(
        self, system: str, transcript: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelResponse:
        recording = self._load()
        digest = request_digest(system, transcript, tools)
        # The turn number is the number of answers already in the transcript, so a retried
        # investigation that starts over lines up with its recording again.
        index = sum(message.role == "assistant" for message in transcript)
        if index < len(recording.turns) and recording.turns[index].digest == digest:
            turn = recording.turns[index]
            self.replayed_turns += 1
            self.turn_costs.append(turn.cost_usd)
            self.last_replayed = True
            return turn.response()

        # Different input, or past the end of the recording: this turn and every later one
        # are made live again.
        del recording.turns[index:]
        response = self._live().respond(system, transcript, tools)
        dollars = cost(response.usage, price_for(self.key.model), batch=self.batch)
        recording.turns.append(_turn(digest, response, dollars, batch=self.batch))
        self.store.save(self.key, recording)
        self.live_turns += 1
        self.turn_costs.append(dollars)
        self.last_replayed = False
        return response

    def _load(self) -> Recording:
        if self._recording is None:
            self._recording = self.store.load(self.key)
        return self._recording

    def _live(self) -> ChatModel:
        if self._live_model is None:
            self._live_model = self.live()
        return self._live_model


def _turn(digest: str, response: ModelResponse, dollars: Decimal, *, batch: bool) -> RecordedTurn:
    usage = response.usage
    return RecordedTurn(
        digest=digest,
        text=response.text,
        tool_calls=tuple((c.id, c.name, c.arguments) for c in response.tool_calls),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        cost_usd=dollars,
        batch=batch,
        raw=_serializable(response.raw),
    )


def _serializable(raw: Any) -> list[dict[str, Any]] | None:
    """Provider content blocks as plain JSON, so a replayed turn can be sent back unchanged."""
    if raw is None:
        return None
    return [
        block.model_dump(mode="json", exclude_none=True)
        if hasattr(block, "model_dump")
        else dict(block)
        for block in raw
    ]
