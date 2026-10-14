"""Running many investigations at once with their model calls sent as Message Batches.

Batch processing costs half as much and suits evaluation, where nobody waits on an answer. An
investigation is a conversation, so each of its turns depends on the last. The coordinator runs
every investigation in its own thread. A thread that needs the model parks its request; when
every running investigation is parked, all their requests go out as one batch, and each thread
resumes with its own answer. A run therefore takes one batch round trip per turn of its longest
investigation.

Production investigations never use this: an operator is waiting on them.
"""

import itertools
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from onramp.agent.anthropic_model import AnthropicChatModel, from_wire
from onramp.agent.graph import DEFAULT_BUDGET, investigate
from onramp.agent.model import Message, ModelResponse, ModelUnavailableError, ToolDefinition
from onramp.evaluation.recordings import RecordedModel, RecordingKey, RecordingStore
from onramp.evaluation.snapshots import InvestigationSnapshot
from onramp.evaluation.spend import MeteredModel, SpendCapReachedError, SpendMeter


@dataclass(frozen=True)
class BatchOutcome:
    message: Any = None  # the provider's message on success
    error: str | None = None


class BatchSubmitter(Protocol):
    def submit(self, requests: dict[str, dict[str, Any]]) -> dict[str, BatchOutcome]:
        """Send the requests as one batch and wait for every result."""
        ...


class AnthropicBatchSubmitter:
    def __init__(
        self,
        client: Any,
        *,
        poll_s: float = 20.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._poll_s = poll_s
        self._sleep = sleep
        self.batch_ids: list[str] = []

    def submit(self, requests: dict[str, dict[str, Any]]) -> dict[str, BatchOutcome]:
        batch = self._client.messages.batches.create(
            requests=[{"custom_id": key, "params": params} for key, params in requests.items()]
        )
        self.batch_ids.append(batch.id)
        while batch.processing_status != "ended":
            self._sleep(self._poll_s)
            batch = self._client.messages.batches.retrieve(batch.id)
        outcomes: dict[str, BatchOutcome] = {}
        for result in self._client.messages.batches.results(batch.id):
            if result.result.type == "succeeded":
                outcomes[result.custom_id] = BatchOutcome(message=result.result.message)
            else:
                outcomes[result.custom_id] = BatchOutcome(error=f"batch {result.result.type}")
        return outcomes


@dataclass
class _Slot:
    params: dict[str, Any]
    outcome: BatchOutcome | None = None


@dataclass
class BatchCoordinator:
    submitter: BatchSubmitter
    batches: int = 0
    requests: int = 0
    _cond: threading.Condition = field(default_factory=threading.Condition)
    _pending: dict[str, _Slot] = field(default_factory=dict)
    _active: int = 0
    _ids: Any = field(default_factory=itertools.count)

    def model(self, inner: AnthropicChatModel) -> "BatchedModel":
        return BatchedModel(inner, self)

    def call(self, params: dict[str, Any]) -> ModelResponse:
        """Park one request until the next batch answers it. Called from a worker thread."""
        with self._cond:
            key = f"r{next(self._ids)}"
            slot = _Slot(params)
            self._pending[key] = slot
            self._cond.notify_all()
            self._cond.wait_for(lambda: slot.outcome is not None)
        outcome = slot.outcome
        if outcome is None:  # unreachable: the wait above returns only once it is set
            raise ModelUnavailableError("batch answered no outcome")
        if outcome.error is not None:
            raise ModelUnavailableError(outcome.error)
        return from_wire(outcome.message)

    def run(self, jobs: Sequence[Callable[[], None]]) -> list[BaseException | None]:
        """Run every job to completion in its own thread; return each job's exception."""
        errors: list[BaseException | None] = [None] * len(jobs)

        def worker(index: int, job: Callable[[], None]) -> None:
            try:
                job()
            except BaseException as exc:  # reported to the caller, never lost
                errors[index] = exc
            finally:
                with self._cond:
                    self._active -= 1
                    self._cond.notify_all()

        with self._cond:
            self._active = len(jobs)
        threads = [
            threading.Thread(target=worker, args=(i, job), daemon=True)
            for i, job in enumerate(jobs)
        ]
        for thread in threads:
            thread.start()
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._active == 0 or len(self._pending) == self._active)
                if self._active == 0:
                    break
                parked = dict(self._pending)
                self._pending.clear()
            outcomes = self._submit(parked)
            with self._cond:
                for key, slot in parked.items():
                    slot.outcome = outcomes.get(key, BatchOutcome(error="missing from batch"))
                self._cond.notify_all()
        for thread in threads:
            thread.join()
        return errors

    def _submit(self, parked: dict[str, _Slot]) -> dict[str, BatchOutcome]:
        self.batches += 1
        self.requests += len(parked)
        try:
            return self.submitter.submit({key: slot.params for key, slot in parked.items()})
        except Exception as exc:
            # A failed submission fails these calls like an unavailable provider; the
            # investigations end, and their unrecorded turns are made live later.
            return {key: BatchOutcome(error=f"{type(exc).__name__}: {exc}") for key in parked}


@dataclass
class BatchedModel:
    """A live model whose calls wait for the coordinator's next batch."""

    inner: AnthropicChatModel
    coordinator: BatchCoordinator

    @property
    def name(self) -> str:
        return self.inner.name

    def respond(
        self, system: str, transcript: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelResponse:
        return self.coordinator.call(self.inner.request(system, transcript, tools))


@dataclass(frozen=True)
class BatchRecordingResult:
    investigations: int
    batches: int
    requests: int
    # Scenario -> why its investigation could not be recorded in full. Those turns are made
    # live, and metered, when the run replays.
    failures: dict[str, str]


def record_investigations(
    snapshots: Sequence[InvestigationSnapshot],
    *,
    model: AnthropicChatModel,
    prompt_version: str,
    store: RecordingStore,
    meter: SpendMeter,
    submitter: BatchSubmitter,
    repeat: int = 0,
    chunk_size: int = 50,
) -> BatchRecordingResult:
    """Record every snapshot's investigation, sending the model calls as batches.

    Snapshots run in chunks, and the spending cap is checked before each chunk, so a run
    overshoots its cap by at most one turn of one chunk.
    """
    coordinator = BatchCoordinator(submitter)
    # Batch latency says nothing about the investigation, so the wall-clock limit is lifted;
    # every other budget limit stays exactly as it is in production.
    budget = replace(DEFAULT_BUDGET, timeout_s=float(24 * 60 * 60))
    failures: dict[str, str] = {}

    def job(snapshot: InvestigationSnapshot) -> Callable[[], None]:
        def run() -> None:
            recorded = RecordedModel(
                RecordingKey(model.name, prompt_version, snapshot.scenario_id, repeat),
                store,
                lambda: coordinator.model(model),
                batch=True,
            )
            result = investigate(
                MeteredModel(recorded, meter, batch=True),
                snapshot.toolbox(),
                snapshot.scope.case_type,
                budget=budget,
                prompt_version=prompt_version,
            )
            if result.status == "model_error":
                failures[snapshot.scenario_id] = "; ".join(result.problems)

        return run

    for start in range(0, len(snapshots), chunk_size):
        meter.check()
        chunk = snapshots[start : start + chunk_size]
        for snapshot, error in zip(chunk, coordinator.run([job(s) for s in chunk]), strict=True):
            if error is not None:
                failures[snapshot.scenario_id] = f"{type(error).__name__}: {error}"
                if isinstance(error, SpendCapReachedError):
                    raise error
    return BatchRecordingResult(len(snapshots), coordinator.batches, coordinator.requests, failures)
