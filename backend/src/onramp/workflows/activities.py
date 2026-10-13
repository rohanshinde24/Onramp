"""Retry-safe Temporal activities over authoritative application services."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity

from onramp.actions.gateway import ActionGateway
from onramp.approvals.models import Approval, Decision
from onramp.audit.service import Actor
from onramp.cases.escalation import EscalationReason
from onramp.cases.models import SupportCase
from onramp.cases.queries import get_case
from onramp.cases.service import transition
from onramp.cases.state import CaseState
from onramp.checks.models import CheckRun
from onramp.checks.service import CheckService
from onramp.investigations.service import InvestigationService
from onramp.proposals.models import Proposal
from onramp.workflows.contracts import CaseSnapshot, CaseWorkflowInput

Clock = Callable[[], datetime]
EVIDENCE_WAIT_TIMEOUT = timedelta(days=7)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WorkflowActivities:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        checks: CheckService,
        gateway: ActionGateway,
        clock: Clock = _utcnow,
    ) -> None:
        self._sessions = sessions
        self._checks = checks
        self._gateway = gateway
        self._clock = clock

    @activity.defn(name="read_case")
    def read_case(self, request: CaseWorkflowInput) -> CaseSnapshot:
        with self._sessions() as session:
            return _snapshot(session, get_case(session, request.tenant_id, request.case_id))

    @activity.defn(name="run_case_checks")
    def run_case_checks(self, request: CaseWorkflowInput) -> CaseSnapshot:
        snapshot = self.read_case(request)
        if snapshot.state not in {CaseState.CREATED, CaseState.INITIAL_CHECKS}:
            return snapshot
        self._checks.run(request.tenant_id, request.case_id, Actor.system())
        return self.read_case(request)

    @activity.defn(name="resume_settlement_checks")
    def resume_settlement_checks(self, request: CaseWorkflowInput) -> CaseSnapshot:
        with self._sessions() as session, session.begin():
            case = get_case(session, request.tenant_id, request.case_id)
            if case.state == CaseState.WAITING_FOR_SETTLEMENT:
                transition(
                    session,
                    case,
                    CaseState.INITIAL_CHECKS,
                    actor=Actor.system(),
                    occurred_at=self._clock(),
                    reason="settlement_deadline_reached",
                )
        return self.read_case(request)

    @activity.defn(name="expire_evidence_wait")
    def expire_evidence_wait(self, request: CaseWorkflowInput) -> CaseSnapshot:
        with self._sessions() as session, session.begin():
            case = get_case(session, request.tenant_id, request.case_id)
            deadline = _evidence_deadline(session, case)
            if (
                case.state == CaseState.WAITING_FOR_EVIDENCE
                and deadline is not None
                and self._clock() >= deadline
            ):
                now = self._clock()
                case.escalation_reason = EscalationReason.EVIDENCE_TIMEOUT.value
                transition(
                    session,
                    case,
                    CaseState.ESCALATED,
                    actor=Actor.system(),
                    occurred_at=now,
                    reason=EscalationReason.EVIDENCE_TIMEOUT.value,
                )
        return self.read_case(request)

    @activity.defn(name="execute_approved_action")
    def execute_approved_action(self, request: CaseWorkflowInput) -> CaseSnapshot:
        snapshot = self.read_case(request)
        if snapshot.state != CaseState.EXECUTING:
            return snapshot
        if snapshot.proposal_id is None:
            raise RuntimeError(f"executing case {request.case_id} has no approved proposal")
        self._gateway.execute(request.tenant_id, snapshot.proposal_id)
        return self.read_case(request)

    @activity.defn(name="recover_action_verification")
    def recover_action_verification(self, request: CaseWorkflowInput) -> CaseSnapshot:
        with self._sessions() as session, session.begin():
            case = get_case(session, request.tenant_id, request.case_id)
            if case.state == CaseState.VERIFYING:
                case.escalation_reason = EscalationReason.POSTCONDITION_FAILED.value
                transition(
                    session,
                    case,
                    CaseState.ESCALATED,
                    actor=Actor.system(),
                    occurred_at=self._clock(),
                    reason=EscalationReason.POSTCONDITION_FAILED.value,
                    details={"recovery": "committed_verifying_state"},
                )
        return self.read_case(request)


class AgentActivities:
    def __init__(
        self, sessions: sessionmaker[Session], investigations: InvestigationService
    ) -> None:
        self._sessions = sessions
        self._investigations = investigations

    @activity.defn(name="run_investigation")
    def run_investigation(self, request: CaseWorkflowInput) -> CaseSnapshot:
        with self._sessions() as session:
            case = get_case(session, request.tenant_id, request.case_id)
            if case.state != CaseState.INVESTIGATING:
                return _snapshot(session, case)
        self._investigations.run(request.tenant_id, request.case_id, Actor.agent())
        with self._sessions() as session:
            return _snapshot(session, get_case(session, request.tenant_id, request.case_id))


def _snapshot(session: Session, case: SupportCase) -> CaseSnapshot:
    latest_check = session.scalar(
        select(CheckRun)
        .filter_by(tenant_id=case.tenant_id, case_id=case.id)
        .order_by(CheckRun.created_at.desc(), CheckRun.id.desc())
        .limit(1)
    )
    proposal_id = session.scalar(
        select(Proposal.id)
        .join(
            Approval,
            (Approval.tenant_id == Proposal.tenant_id) & (Approval.proposal_id == Proposal.id),
        )
        .where(
            Proposal.tenant_id == case.tenant_id,
            Proposal.case_id == case.id,
            Approval.decision == Decision.APPROVED.value,
        )
        .order_by(Approval.decided_at.desc(), Approval.id.desc())
        .limit(1)
    )
    return CaseSnapshot(
        state=case.state,
        wait_until=(
            latest_check.wait_until
            if latest_check and latest_check.wait_until is not None
            else _evidence_deadline(session, case, latest_check)
        ),
        proposal_id=proposal_id,
    )


def _evidence_deadline(
    session: Session, case: SupportCase, latest_check: CheckRun | None = None
) -> datetime | None:
    if case.state != CaseState.WAITING_FOR_EVIDENCE:
        return None
    if latest_check is None:
        latest_check = session.scalar(
            select(CheckRun)
            .filter_by(tenant_id=case.tenant_id, case_id=case.id)
            .order_by(CheckRun.created_at.desc(), CheckRun.id.desc())
            .limit(1)
        )
    return latest_check.created_at + EVIDENCE_WAIT_TIMEOUT if latest_check else None
