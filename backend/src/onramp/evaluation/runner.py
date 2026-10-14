"""Runs one evaluation scenario through the real application services.

For each scenario the runner empties the evaluation databases, builds the scenario's world,
opens the case through intake as the marketplace would, and then drives the case the way the
durable workflow does: the same activity implementations, chosen by the same case states. A
virtual clock replaces the Temporal timer, so a settlement or evidence wait that would take
days in production ends at its deadline here. Temporal's own guarantees (durable timers,
signals, recovery) are covered by the workflow tests, not by this runner.

Where a person would act, a scripted reviewer stands in: the operator accepts the pending
proposal and a lead approves the accepted action, exactly as shown. The evaluation therefore
measures what the system recommends and executes when nobody overrides it.

Each run ends in one of three statuses (ADR-009):

* ``success``: the case reached a state where the system stops on its own,
* ``app_failure``: the application raised, or the case stopped making progress,
* ``harness_error``: the harness could not set up or observe the scenario.

Only a successful run is graded as a case outcome; the others are counted separately so a
broken harness cannot look like a wrong answer.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from sqlalchemy.orm import Session, sessionmaker

from onramp.actions.gateway import ActionExecutionFailedError, GatewayRejectedError
from onramp.agent.model import ChatModel
from onramp.cases.service import IntakeError, IntakeRequest
from onramp.cases.state import CaseState
from onramp.evaluation.case_grading import CaseObservation
from onramp.evaluation.faults import (
    INJECTED_ERRORS,
    InjectedCrashError,
    Retry,
    ScenarioFaults,
    no_faults,
)
from onramp.evaluation.observations import observe_case
from onramp.evaluation.reviewers import HonestReviewer, ReviewContext, honest_reviewers
from onramp.evaluation.safety import safety_violations
from onramp.evaluation.scenarios import ScenarioInput
from onramp.evaluation.services import CaseServices, ServiceFactory, VirtualClock
from onramp.evaluation.worlds import SERVICE, TENANT, WorldEvent, build_world, happen
from onramp.events.consumer import ConsumerRunner, Outcome, RawMessage
from onramp.events.contracts import contract_for
from onramp.identity.principal import Principal
from onramp.identity.roles import Role
from onramp.policy.corpus import PolicyDocument
from onramp.workflows.case import ACTIVITY_RETRY, INVESTIGATION_RETRY
from onramp.workflows.contracts import CaseSnapshot, CaseWorkflowInput

DEFAULT_MAX_STEPS = 40
_STOP_STATES = frozenset({CaseState.RESOLVED, CaseState.CLOSED_NO_ACTION, CaseState.ESCALATED})
# Retries as the workflow's policies make them. Unlimited investigation retries become five,
# because a scripted provider that never recovers must still end the run.
ACTIVITY = Retry.mirroring(ACTIVITY_RETRY, unlimited_cap=5)
INVESTIGATION = Retry.mirroring(INVESTIGATION_RETRY, unlimited_cap=5)


class RunStatus(StrEnum):
    SUCCESS = "success"
    APP_FAILURE = "app_failure"
    # The application did not recover from a fault the harness injected.
    INJECTED_FAULT = "injected_fault"
    HARNESS_ERROR = "harness_error"


@dataclass
class ScenarioRun:
    scenario_id: str
    status: RunStatus
    case_id: str | None = None
    final_state: str | None = None
    steps: list[str] = field(default_factory=list)
    faults: list[str] = field(default_factory=list)
    error: str | None = None
    observation: CaseObservation | None = None
    wall_ms: int = 0


class StalledCaseError(RuntimeError):
    """The case stopped making progress, or used up the step budget."""


@dataclass
class _Case:
    """Everything one scenario's drive needs."""

    services: CaseServices
    reviewer: HonestReviewer
    review: ReviewContext
    clock: VirtualClock
    events: list[WorldEvent]
    faults: ScenarioFaults

    @property
    def steps(self) -> list[str]:
        return self.review.steps


class CaseEvaluationRunner:
    def __init__(
        self,
        onramp: sessionmaker[Session],
        marketplace: sessionmaker[Session],
        reset: Callable[[], None],
        services: ServiceFactory,
        corpus: list[PolicyDocument],
        models: Callable[[str], ChatModel],
        *,
        reviewers: Callable[[str], HonestReviewer] = honest_reviewers,
        faults: Callable[[str], ScenarioFaults] = no_faults,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> None:
        self._onramp = onramp
        self._marketplace = marketplace
        self._reset = reset
        self._services = services
        self._corpus = corpus
        self._models = models
        self._reviewers = reviewers
        self._faults = faults
        self._max_steps = max_steps

    def run(self, scenario: ScenarioInput) -> ScenarioRun:
        started = time.perf_counter()
        run = ScenarioRun(scenario.id, RunStatus.HARNESS_ERROR)
        try:
            self._reset()
            world = build_world(
                scenario.world_fixture, self._onramp, self._marketplace, self._corpus
            )
            clock = VirtualClock(world.opened_at)
            faults = self._faults(scenario.id)
            run.faults = faults.labels
            model = faults.wrap_model(self._models(scenario.id))
            services = self._services(clock, model, faults)
            reviewer = self._reviewers(scenario.id)
        except Exception as exc:  # any setup failure is the harness's
            run.error = f"setup: {type(exc).__name__}: {exc}"
            return _finish(run, started)

        try:
            run.case_id = self._open(services, scenario)
            case = _Case(
                services,
                reviewer,
                ReviewContext(services, self._onramp, run.case_id, run.steps),
                clock,
                sorted(world.events, key=lambda event: event.at),
                faults,
            )
            run.final_state = self._drive(case)
            reviewer.after_stop(case.review)
            run.final_state = self._after_stop(case)
            run.status = RunStatus.SUCCESS
        except IntakeError as exc:
            # Refusing a report is a behavior to grade, not a failure to run.
            run.steps.append(f"intake_refused:{type(exc).__name__}")
            run.status = RunStatus.SUCCESS
        except Exception as exc:  # recorded; the scenario is still observed
            injected = bool(run.faults) and isinstance(exc, INJECTED_ERRORS)
            run.status = RunStatus.INJECTED_FAULT if injected else RunStatus.APP_FAILURE
            run.error = f"{type(exc).__name__}: {exc}"

        if run.case_id is None and run.status is not RunStatus.SUCCESS:
            return _finish(run, started)  # failed before intake decided anything
        try:
            run.observation = self._observe(
                scenario, run.case_id, [f"attack_allowed:{name}" for name in reviewer.allowed]
            )
        except Exception as exc:  # observation failure is the harness's
            run.status = RunStatus.HARNESS_ERROR
            run.error = f"observe: {type(exc).__name__}: {exc}"
        return _finish(run, started)

    def _open(self, services: CaseServices, scenario: ScenarioInput) -> str:
        result = services.cases.open_case(
            Principal(TENANT, SERVICE, Role.MARKETPLACE_SERVICE),
            IntakeRequest(
                case_type=scenario.case_type,
                subject_id=scenario.subject_id,
                intake_key=f"eval-{scenario.id}",
                description=scenario.report,
                claimed_worker_id=scenario.claimed_worker_id,
            ),
        )
        return result.case.id

    def _drive(self, case: _Case) -> str:
        request = CaseWorkflowInput(TENANT, case.review.case_id)
        workflow = case.services.workflow
        for _ in range(self._max_steps):
            case.clock.tick()
            snapshot = workflow.read_case(request)
            state = CaseState(snapshot.state)
            case.steps.append(state.value)
            if state in _STOP_STATES:
                return state.value
            match state:
                case CaseState.CREATED | CaseState.INITIAL_CHECKS:
                    self._activity(
                        case, "run_case_checks", lambda: workflow.run_case_checks(request)
                    )
                case CaseState.INVESTIGATING:
                    after = self._activity(
                        case,
                        "run_investigation",
                        lambda: case.services.agent.run_investigation(request),
                        INVESTIGATION,
                    )
                    if after.state == CaseState.INVESTIGATING:
                        raise StalledCaseError("investigation did not conclude")
                case CaseState.WAITING_FOR_SETTLEMENT | CaseState.WAITING_FOR_EVIDENCE:
                    deadline = _deadline(snapshot)
                    if case.events and case.events[0].at < deadline:
                        # Something happens in the marketplace before the wait ends. It reaches
                        # the case the way the relay and evidence consumer would deliver it.
                        self._deliver(case, case.events.pop(0))
                    elif state is CaseState.WAITING_FOR_SETTLEMENT:
                        case.clock.advance_to(deadline)
                        self._activity(
                            case,
                            "resume_settlement_checks",
                            lambda: workflow.resume_settlement_checks(request),
                        )
                    else:
                        case.clock.advance_to(deadline)
                        self._activity(
                            case,
                            "expire_evidence_wait",
                            lambda: workflow.expire_evidence_wait(request),
                        )
                case CaseState.PROPOSED:
                    case.reviewer.on_proposed(case.review)
                case CaseState.AWAITING_APPROVAL:
                    case.reviewer.on_awaiting_approval(case.review)
                case CaseState.EXECUTING:
                    case.reviewer.before_execution(case.review)
                    self._activity(
                        case, "execute_approved_action", lambda: self._execute(case, request)
                    )
                case CaseState.VERIFYING:
                    self._activity(
                        case,
                        "recover_action_verification",
                        lambda: workflow.recover_action_verification(request),
                    )
                case _:
                    raise StalledCaseError(f"no step for state {state.value}")
        raise StalledCaseError(f"no stopping state within {self._max_steps} steps")

    def _activity[T](
        self, case: _Case, name: str, call: Callable[[], T], retry: Retry = ACTIVITY
    ) -> T:
        """Run one workflow activity with the retries Temporal would make."""
        for attempt in range(1, retry.attempts + 1):
            try:
                result = call()
                if case.faults.crash_after(name):
                    case.steps.append(f"fault:crash_after:{name}")
                    raise InjectedCrashError(f"process died after {name} committed")
                return result
            except Exception as exc:
                if attempt == retry.attempts:
                    raise
                case.steps.append(f"retry:{name}:{type(exc).__name__}")
                case.clock.advance_to(case.clock() + retry.delay(attempt))
        raise AssertionError("unreachable: the last attempt returns or raises")

    def _execute(self, case: _Case, request: CaseWorkflowInput) -> CaseSnapshot:
        try:
            return case.services.workflow.execute_approved_action(request)
        except (GatewayRejectedError, ActionExecutionFailedError) as exc:
            # The gateway has already recorded the failure and escalated the case.
            case.steps.append(f"gateway:{type(exc).__name__}:{exc}")
            return case.services.workflow.read_case(request)

    def _after_stop(self, case: _Case) -> str:
        """Events that happen after the case stopped still happen. The case must not move."""
        while case.events:
            self._deliver(case, case.events.pop(0))
        snapshot = case.services.workflow.read_case(CaseWorkflowInput(TENANT, case.review.case_id))
        return snapshot.state

    def _deliver(self, case: _Case, event: WorldEvent) -> None:
        case.clock.advance_to(event.at)
        envelope = happen(event, self._marketplace)
        consumer = ConsumerRunner(
            self._onramp, _OneMessage(), _NoDeadLetters(), case.services.evidence
        )
        message = RawMessage(
            topic=contract_for(envelope.event_type, envelope.schema_version).topic,
            partition=0,
            offset=0,
            key=envelope.partition_key.encode(),
            value=envelope.to_json(),
        )
        deliveries = 2 if case.faults.duplicate_events else 1
        for _ in range(deliveries):
            outcome = consumer.process(message)
            case.steps.append(f"event:{envelope.event_type}:{outcome.value}")
            if outcome is Outcome.PARKED:
                raise StalledCaseError(f"{envelope.event_type} was parked")

    def _observe(
        self, scenario: ScenarioInput, case_id: str | None, attacks_allowed: list[str]
    ) -> CaseObservation:
        with self._onramp() as onramp, self._marketplace() as marketplace:
            violations = [*safety_violations(onramp, marketplace, TENANT), *attacks_allowed]
            if case_id is None:
                return CaseObservation(
                    scenario_id=scenario.id,
                    case_type=scenario.case_type,
                    intake_refused=True,
                    safety_violations=tuple(violations),
                )
            return observe_case(onramp, TENANT, case_id, scenario.id, safety_violations=violations)


def _deadline(snapshot: CaseSnapshot) -> datetime:
    if snapshot.wait_until is None:
        raise StalledCaseError(f"{snapshot.state} has no deadline")
    return snapshot.wait_until


def _finish(run: ScenarioRun, started: float) -> ScenarioRun:
    run.wall_ms = round((time.perf_counter() - started) * 1000)
    return run


class _OneMessage:
    """The consumer's message source for messages the runner hands it directly."""

    def poll(self, timeout_s: float) -> RawMessage | None:
        return None

    def commit(self, message: RawMessage) -> None:
        return None

    def close(self) -> None:
        return None


class _NoDeadLetters:
    def park(self, message: RawMessage, *, reason: str, error: str, group: str) -> None:
        return None
