from types import SimpleNamespace
from typing import Any

from onramp.agent.anthropic_model import AnthropicChatModel
from onramp.agent.model import Message, ModelUnavailableError
from onramp.evaluation.batching import BatchCoordinator, BatchOutcome


def _message(text: str) -> Any:
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=2),
    )


class _FakeSubmitter:
    """Answers each request with the last user text it carries, and records batch sizes."""

    def __init__(self, fail_batch: int | None = None) -> None:
        self.sizes: list[int] = []
        self._fail_batch = fail_batch

    def submit(self, requests: dict[str, dict[str, Any]]) -> dict[str, BatchOutcome]:
        self.sizes.append(len(requests))
        if self._fail_batch == len(self.sizes):
            raise ConnectionError("batch endpoint down")
        return {
            key: BatchOutcome(message=_message(params["messages"][-1]["content"]))
            for key, params in requests.items()
        }


def _model(coordinator: BatchCoordinator) -> Any:
    return coordinator.model(AnthropicChatModel(client=object(), model="claude-haiku-5-5"))


def test_every_parked_call_goes_out_together_once_per_turn() -> None:
    submitter = _FakeSubmitter()
    coordinator = BatchCoordinator(submitter)
    answers: dict[str, list[str]] = {}

    def job(name: str, turns: int) -> Any:
        def run() -> None:
            model = _model(coordinator)
            answers[name] = [
                model.respond("system", [Message("user", text=f"{name}-{turn}")], []).text
                for turn in range(turns)
            ]

        return run

    errors = coordinator.run([job("a", 1), job("b", 3), job("c", 2)])

    assert errors == [None, None, None]
    assert answers == {"a": ["a-0"], "b": ["b-0", "b-1", "b-2"], "c": ["c-0", "c-1"]}
    assert submitter.sizes == [3, 2, 1]  # one batch per turn of the longest conversation
    assert (coordinator.batches, coordinator.requests) == (3, 6)


def test_a_failed_batch_fails_those_calls_as_an_unavailable_provider() -> None:
    coordinator = BatchCoordinator(_FakeSubmitter(fail_batch=1))

    def job() -> None:
        _model(coordinator).respond("system", [Message("user", text="x")], [])

    (error,) = coordinator.run([job])

    assert isinstance(error, ModelUnavailableError)
    assert "batch endpoint down" in str(error)
