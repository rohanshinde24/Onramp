"""Opening cases and moving them through the lifecycle.

Intake trusts nothing about identity from the request. The worker, and the time used to pick
the applicable policy, come from marketplace records.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from onramp.audit.models import ActorType
from onramp.audit.service import Actor, record_audit
from onramp.cases.escalation import EscalationReason
from onramp.cases.models import SUBJECT_TYPE, CaseType, SupportCase
from onramp.cases.queries import get_case
from onramp.cases.state import TERMINAL_STATES, CaseState, require_transition
from onramp.db.types import new_id
from onramp.identity.principal import Principal
from onramp.identity.roles import Permission, Role
from onramp.marketplace.reader import MarketplaceReader

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class IntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_type: CaseType
    subject_id: str = Field(min_length=1, max_length=64)
    intake_key: str = Field(min_length=1, max_length=200)
    description: str = Field(max_length=10_000)
    # Optional cross-check from the reporter. Never used as the case's worker.
    claimed_worker_id: str | None = Field(default=None, max_length=64)


class IntakeError(Exception):
    pass


class SubjectNotFoundError(IntakeError):
    """The subject does not exist in the caller's tenant (or exists only in another)."""


class WorkerMismatchError(IntakeError):
    """The claimed worker is not the worker the subject belongs to."""


class IntakeKeyConflictError(IntakeError):
    """The intake key was already used for a different case."""


@dataclass(frozen=True)
class IntakeResult:
    case: SupportCase
    created: bool
    attached_to_existing: bool = False


@dataclass(frozen=True)
class _Subject:
    worker_id: str
    incident_at: datetime
    incident_basis: str


class CaseService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        marketplace: MarketplaceReader,
        clock: Clock = _utcnow,
    ) -> None:
        self._sessions = sessions
        self._marketplace = marketplace
        self._clock = clock

    def open_case(self, principal: Principal, request: IntakeRequest) -> IntakeResult:
        principal.require(Permission.CASE_CREATE)
        subject = self._resolve_subject(principal.tenant_id, request)
        if request.claimed_worker_id and request.claimed_worker_id != subject.worker_id:
            raise WorkerMismatchError(request.subject_id)

        try:
            return self._open(principal, request, subject)
        except IntegrityError:
            # A concurrent intake won the race on the intake key or the open-subject index.
            # Its case now exists, so a second pass resolves to it.
            return self._open(principal, request, subject)

    def _open(
        self, principal: Principal, request: IntakeRequest, subject: _Subject
    ) -> IntakeResult:
        now = self._clock()
        actor = _actor(principal)
        with self._sessions() as session, session.begin():
            existing = session.scalar(
                select(SupportCase).filter_by(
                    tenant_id=principal.tenant_id, intake_key=request.intake_key
                )
            )
            if existing is not None:
                if (existing.case_type, existing.subject_id) != (
                    request.case_type.value,
                    request.subject_id,
                ):
                    raise IntakeKeyConflictError(request.intake_key)
                return IntakeResult(existing, created=False)

            open_case = session.scalar(
                select(SupportCase).where(
                    SupportCase.tenant_id == principal.tenant_id,
                    SupportCase.case_type == request.case_type.value,
                    SupportCase.subject_id == request.subject_id,
                    SupportCase.state.not_in([s.value for s in TERMINAL_STATES]),
                )
            )
            if open_case is not None:
                record_audit(
                    session,
                    tenant_id=principal.tenant_id,
                    actor=actor,
                    subject_type="case",
                    subject_id=open_case.id,
                    action="case.report_attached",
                    occurred_at=now,
                    details={
                        "intake_key": request.intake_key,
                        "description": request.description,
                    },
                )
                return IntakeResult(open_case, created=False, attached_to_existing=True)

            case = SupportCase(
                id=new_id("case"),
                tenant_id=principal.tenant_id,
                case_type=request.case_type.value,
                state=CaseState.CREATED.value,
                subject_type=SUBJECT_TYPE[request.case_type],
                subject_id=request.subject_id,
                worker_id=subject.worker_id,
                incident_at=subject.incident_at,
                incident_basis=subject.incident_basis,
                intake_key=request.intake_key,
                reported_description=request.description,
                opened_by_type=actor.type.value,
                opened_by_id=principal.user_id,
                created_at=now,
                updated_at=now,
            )
            session.add(case)
            session.flush()
            record_audit(
                session,
                tenant_id=principal.tenant_id,
                actor=actor,
                subject_type="case",
                subject_id=case.id,
                action="case.created",
                occurred_at=now,
                details={
                    "case_type": case.case_type,
                    "subject": f"{case.subject_type}:{case.subject_id}",
                    "worker_id": case.worker_id,
                    "incident_at": case.incident_at.isoformat(),
                    "incident_basis": case.incident_basis,
                },
            )
            return IntakeResult(case, created=True)

    def escalate(
        self,
        principal: Principal,
        case_id: str,
        reason: EscalationReason,
        *,
        expected_version: int,
        note: str = "",
    ) -> SupportCase:
        principal.require(Permission.CASE_ESCALATE)
        with self._sessions() as session, session.begin():
            case = get_case(session, principal.tenant_id, case_id)
            if case.version != expected_version:
                raise StaleCaseError(case_id)
            case.escalation_reason = reason.value
            transition(
                session,
                case,
                CaseState.ESCALATED,
                actor=_actor(principal),
                occurred_at=self._clock(),
                reason=reason.value,
                details={"note": note} if note else None,
            )
            return case

    def _resolve_subject(self, tenant_id: str, request: IntakeRequest) -> _Subject:
        mp = self._marketplace
        if request.case_type is CaseType.ONBOARDING:
            worker = mp.get_worker(tenant_id, request.subject_id)
            if worker is None:
                raise SubjectNotFoundError(request.subject_id)
            # Onboarding requirements are judged as of when the worker raised the problem.
            return _Subject(worker.id, self._clock(), "case.reported_at")

        if request.case_type is CaseType.QUALITY_REVIEW:
            review = mp.get_quality_review(tenant_id, request.subject_id)
            if review is None:
                raise SubjectNotFoundError(request.subject_id)
            submission_id = review.submission_id
            incident_at, basis = review.reviewed_at, "review.reviewed_at"
        else:
            submission_id = request.subject_id

        submission = mp.get_submission(tenant_id, submission_id)
        if submission is None:
            raise SubjectNotFoundError(request.subject_id)
        assignment = mp.get_assignment(tenant_id, submission.assignment_id)
        if assignment is None:
            raise SubjectNotFoundError(request.subject_id)
        if request.case_type is CaseType.PAYOUT:
            incident_at, basis = submission.submitted_at, "submission.submitted_at"
        return _Subject(assignment.worker_id, incident_at, basis)


class StaleCaseError(Exception):
    """The case changed since the caller last read it."""


def _actor(principal: Principal) -> Actor:
    if principal.role is Role.MARKETPLACE_SERVICE:
        return Actor(ActorType.MARKETPLACE_SERVICE, principal.user_id)
    return Actor.user(principal.user_id)


def transition(
    session: Session,
    case: SupportCase,
    target: CaseState,
    *,
    actor: Actor,
    occurred_at: datetime,
    reason: str,
    details: dict[str, object] | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> None:
    """Move a case to ``target`` in the caller's transaction, with an audit event.

    The version column makes a concurrent transition of the same case fail at flush.
    """
    current = CaseState(case.state)
    require_transition(current, target)
    case.state = target.value
    case.updated_at = occurred_at
    record_audit(
        session,
        tenant_id=case.tenant_id,
        actor=actor,
        subject_type="case",
        subject_id=case.id,
        action="case.transitioned",
        occurred_at=occurred_at,
        details={"from": current.value, "to": target.value, "reason": reason, **(details or {})},
        correlation_id=correlation_id,
        causation_id=causation_id,
    )
    session.flush()
