"""Faults injected into a case evaluation, per scenario.

A fault suite names, for each scenario, what goes wrong while its case runs:

* ``marketplace``: a marketplace write times out before reaching the marketplace, or lands and
  its response is lost, on chosen call numbers,
* ``model_outage``: the model provider fails a number of times before answering,
* ``crash_after``: a workflow activity commits its work and then the process dies before
  reporting success, a number of times,
* ``duplicate_events``: every marketplace event is delivered twice, as at-least-once delivery
  allows.

The runner retries failed activities the way the Temporal workflow's retry policies do, so a
scenario shows whether the application recovers, not merely whether it fails.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from temporalio.common import RetryPolicy

from onramp.agent.model import (
    ChatModel,
    Message,
    ModelResponse,
    ModelUnavailableError,
    ToolDefinition,
)
from onramp.marketplace.faults import Fault, FaultPlan, OutcomeUnknownError
from onramp.workflows.activities import InvestigationUnavailableError


class InjectedCrashError(RuntimeError):
    """The process died after an activity's work committed, before it reported success."""


# Errors the harness itself causes. A run that ends on one of these, after the retries a
# workflow would make, is an injected fault rather than an application failure.
INJECTED_ERRORS: tuple[type[Exception], ...] = (
    OutcomeUnknownError,
    InjectedCrashError,
    InvestigationUnavailableError,
)


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MarketplaceFault(_Spec):
    kind: Literal["marketplace"]
    operation: Literal["issue_adjustment", "activate_worker", "overturn_review", "request_document"]
    calls: tuple[int, ...] = Field(min_length=1)
    fault: Fault


class ModelOutage(_Spec):
    kind: Literal["model_outage"]
    failures: int = Field(ge=1)


class CrashAfter(_Spec):
    kind: Literal["crash_after"]
    activity: Literal[
        "run_case_checks",
        "run_investigation",
        "execute_approved_action",
        "resume_settlement_checks",
        "expire_evidence_wait",
    ]
    times: int = Field(default=1, ge=1)


class DuplicateEvents(_Spec):
    kind: Literal["duplicate_events"]


FaultSpec = Annotated[
    MarketplaceFault | ModelOutage | CrashAfter | DuplicateEvents, Field(discriminator="kind")
]


class FaultSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    faults: dict[str, tuple[FaultSpec, ...]]


@dataclass
class ScenarioFaults:
    """The faults for one scenario run. Stateful: each fault is used up as it fires."""

    specs: tuple[FaultSpec, ...] = ()
    _crashes: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for spec in self.specs:
            if isinstance(spec, CrashAfter):
                self._crashes[spec.activity] = self._crashes.get(spec.activity, 0) + spec.times

    @property
    def labels(self) -> list[str]:
        return [_label(spec) for spec in self.specs]

    def marketplace_plan(self) -> FaultPlan | None:
        specs = [s for s in self.specs if isinstance(s, MarketplaceFault)]
        if not specs:
            return None
        plan = FaultPlan()
        for spec in specs:
            for call in spec.calls:
                plan.inject(spec.operation, call, spec.fault)
        return plan

    def wrap_model(self, model: ChatModel) -> ChatModel:
        failures = sum(s.failures for s in self.specs if isinstance(s, ModelOutage))
        return FlakyModel(model, failures) if failures else model

    def crash_after(self, activity: str) -> bool:
        """True, once per configured crash, right after ``activity`` succeeded."""
        remaining = self._crashes.get(activity, 0)
        if remaining:
            self._crashes[activity] = remaining - 1
        return bool(remaining)

    @property
    def duplicate_events(self) -> bool:
        return any(isinstance(s, DuplicateEvents) for s in self.specs)


def no_faults(_: str) -> ScenarioFaults:
    return ScenarioFaults()


def load_faults(path: Path) -> Callable[[str], ScenarioFaults]:
    """Scenario ID -> a fresh set of faults; scenarios not listed run without faults."""
    named = FaultSet.model_validate(yaml.safe_load(path.read_text())).faults
    return lambda scenario_id: ScenarioFaults(named.get(scenario_id, ()))


class FlakyModel:
    """Fails like an unavailable provider a fixed number of times, then delegates."""

    def __init__(self, inner: ChatModel, failures: int) -> None:
        self._inner = inner
        self._failures = failures

    @property
    def name(self) -> str:
        return self._inner.name

    def respond(
        self, system: str, transcript: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelResponse:
        if self._failures:
            self._failures -= 1
            raise ModelUnavailableError("injected provider outage")
        return self._inner.respond(system, transcript, tools)


@dataclass(frozen=True)
class Retry:
    """A Temporal retry policy, applied by the runner on a virtual clock."""

    attempts: int
    initial: timedelta
    coefficient: float
    maximum: timedelta

    @classmethod
    def mirroring(cls, policy: RetryPolicy, unlimited_cap: int) -> "Retry":
        # Temporal's defaults: unlimited attempts and a maximum interval of 100x the initial.
        # An evaluation must end, so unlimited becomes ``unlimited_cap``.
        return cls(
            attempts=policy.maximum_attempts or unlimited_cap,
            initial=policy.initial_interval,
            coefficient=policy.backoff_coefficient,
            maximum=policy.maximum_interval or policy.initial_interval * 100,
        )

    def delay(self, attempt: int) -> timedelta:
        """Wait after failed attempt number ``attempt`` (1-based)."""
        return min(self.initial * self.coefficient ** (attempt - 1), self.maximum)


def _label(spec: FaultSpec) -> str:
    match spec:
        case MarketplaceFault():
            calls = ",".join(str(c) for c in spec.calls)
            return f"marketplace:{spec.operation}:{spec.fault.value}@{calls}"
        case ModelOutage():
            return f"model_outage:{spec.failures}"
        case CrashAfter():
            return f"crash_after:{spec.activity}x{spec.times}"
        case DuplicateEvents():
            return "duplicate_events"
