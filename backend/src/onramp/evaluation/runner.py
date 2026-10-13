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
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.actions.gateway import ActionExecutionFailedError, GatewayRejectedError
from onramp.agent.model import ChatModel
from onramp.approvals.models import Decision
from onramp.approvals.service import ApprovalService
from onramp.cases.service import CaseService, IntakeError, IntakeRequest
from onramp.cases.state import CaseState
from onramp.evaluation.case_grading import CaseObservation
from onramp.evaluation.observations import observe_case
from onramp.evaluation.safety import safety_violations
from onramp.evaluation.scenarios import ScenarioInput
from onramp.evaluation.worlds import LEAD, OPERATOR, SERVICE, TENANT, build_world
from onramp.identity.principal import Principal
from onramp.identity.roles import Role
from onramp.policy.corpus import PolicyDocument
from onramp.proposals.models import Proposal, ProposalStatus
from onramp.proposals.service import ProposalService
from onramp.workflows.activities import AgentActivities, WorkflowActivities
from onramp.workflows.contracts import CaseSnapshot, CaseWorkflowInput

DEFAULT_MAX_STEPS = 40
# Every step happens one second after the previous one, so records written by successive
# steps never share a timestamp.
STEP = timedelta(seconds=1)
_STOP_STATES = frozenset({CaseState.RESOLVED, CaseState.CLOSED_NO_ACTION, CaseState.ESCALATED})


class RunStatus(StrEnum):
    SUCCESS = "success"
    APP_FAILURE = "app_failure"
    HARNESS_ERROR = "harness_error"


class VirtualClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def tick(self) -> None:
        self._now += STEP

    def advance_to(self, deadline: datetime) -> None:
        self._now = max(self._now, deadline)


@dataclass(frozen=True)
class CaseServices:
    cases: CaseService
    workflow: WorkflowActivities
    agent: AgentActivities
    proposals: ProposalService
    approvals: ApprovalService


class ServiceFactory(Protocol):
    def __call__(self, clock: VirtualClock, model: ChatModel) -> CaseServices: ...


@dataclass
class ScenarioRun:
    scenario_id: str
    status: RunStatus
    case_id: str | None = None
    final_state: str | None = None
    steps: list[str] = field(default_factory=list)
    error: str | None = None
    observation: CaseObservation | None = None
    wall_ms: int = 0


class StalledCaseError(RuntimeError):
    """The case stopped making progress, or used up the step budget."""


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
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> None:
        self._onramp = onramp
        self._marketplace = marketplace
        self._reset = reset
        self._services = services
        self._corpus = corpus
        self._models = models
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
            services = self._services(clock, self._models(scenario.id))
        except Exception as exc:  # any setup failure is the harness's
            run.error = f"setup: {type(exc).__name__}: {exc}"
            return _finish(run, started)

        try:
            run.case_id = self._open(services, scenario)
            run.final_state = self._drive(services, clock, run.case_id, run.steps)
            run.status = RunStatus.SUCCESS
        except IntakeError as exc:
            # Refusing a report is a behavior to grade, not a failure to run.
            run.steps.append(f"intake_refused:{type(exc).__name__}")
            run.status = RunStatus.SUCCESS
        except Exception as exc:  # recorded; the scenario is still observed
            run.status = RunStatus.APP_FAILURE
            run.error = f"{type(exc).__name__}: {exc}"

        if run.case_id is None and run.status is not RunStatus.SUCCESS:
            return _finish(run, started)  # failed before intake decided anything
        try:
            run.observation = self._observe(scenario, run.case_id)
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

    def _drive(
        self, services: CaseServices, clock: VirtualClock, case_id: str, steps: list[str]
    ) -> str:
        request = CaseWorkflowInput(TENANT, case_id)
        workflow = services.workflow
        for _ in range(self._max_steps):
            clock.tick()
            snapshot = workflow.read_case(request)
            state = CaseState(snapshot.state)
            steps.append(state.value)
            if state in _STOP_STATES:
                return state.value
            match state:
                case CaseState.CREATED | CaseState.INITIAL_CHECKS:
                    workflow.run_case_checks(request)
                case CaseState.INVESTIGATING:
                    # A provider failure raises so the workflow retries with backoff. A script
                    # never recovers, so here it ends the run as an application failure.
                    after = services.agent.run_investigation(request)
                    if after.state == CaseState.INVESTIGATING:
                        raise StalledCaseError("investigation did not conclude")
                case CaseState.WAITING_FOR_SETTLEMENT:
                    clock.advance_to(_deadline(snapshot))
                    workflow.resume_settlement_checks(request)
                case CaseState.WAITING_FOR_EVIDENCE:
                    clock.advance_to(_deadline(snapshot))
                    workflow.expire_evidence_wait(request)
                case CaseState.PROPOSED:
                    services.proposals.accept(
                        Principal(TENANT, OPERATOR, Role.OPERATOR),
                        self._proposal(case_id, ProposalStatus.PENDING).id,
                    )
                case CaseState.AWAITING_APPROVAL:
                    proposal = self._proposal(case_id, ProposalStatus.ACCEPTED)
                    services.approvals.decide(
                        Principal(TENANT, LEAD, Role.LEAD),
                        proposal.id,
                        Decision.APPROVED,
                        content_hash=proposal.content_hash,
                        note="evaluation reviewer approves the proposal as shown",
                    )
                case CaseState.EXECUTING:
                    try:
                        workflow.execute_approved_action(request)
                    except (GatewayRejectedError, ActionExecutionFailedError) as exc:
                        # The gateway has already recorded the failure and escalated the case.
                        steps.append(f"gateway:{type(exc).__name__}:{exc}")
                case CaseState.VERIFYING:
                    workflow.recover_action_verification(request)
                case _:
                    raise StalledCaseError(f"no step for state {state.value}")
        raise StalledCaseError(f"no stopping state within {self._max_steps} steps")

    def _proposal(self, case_id: str, status: ProposalStatus) -> Proposal:
        with self._onramp() as session:
            proposal = session.scalar(
                select(Proposal)
                .filter_by(tenant_id=TENANT, case_id=case_id, status=status.value)
                .order_by(Proposal.created_at.desc(), Proposal.id.desc())
                .limit(1)
            )
        if proposal is None:
            raise StalledCaseError(f"no {status.value} proposal for case {case_id}")
        return proposal

    def _observe(self, scenario: ScenarioInput, case_id: str | None) -> CaseObservation:
        with self._onramp() as onramp, self._marketplace() as marketplace:
            violations = safety_violations(onramp, marketplace, TENANT)
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
