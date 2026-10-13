"""Creating and deciding proposals.

A proposal from a person is that person's decision, so it is accepted on creation. A proposal
from the agent waits (PENDING) until a person accepts or rejects it. Accepting a proposal
either resolves the case (no action) or sends it for approval (any action).
"""

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.audit.service import Actor, record_audit
from onramp.cases.escalation import EscalationReason
from onramp.cases.models import CaseType, SupportCase
from onramp.cases.queries import get_case
from onramp.cases.resolutions import ACTION_RISK, ActionType, required_action
from onramp.cases.service import StaleCaseError, transition
from onramp.cases.state import CaseState
from onramp.db.types import new_id
from onramp.identity.models import Tenant
from onramp.identity.principal import Principal
from onramp.identity.roles import Permission
from onramp.proposals.content import ACTION_PARAMS, ProposalRequest, content_hash
from onramp.proposals.models import Proposal, ProposalStatus

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class InvalidProposalError(Exception):
    """The proposal does not fit the case: wrong resolution, missing or extra action, bad
    parameters."""


class ProposalNotFoundError(Exception):
    pass


class ProposalNotPendingError(Exception):
    pass


def create_proposal(
    session: Session,
    case: SupportCase,
    request: ProposalRequest,
    *,
    proposer: Actor,
    occurred_at: datetime,
) -> Proposal:
    """Validate a proposal against its case and persist it as PENDING, moving the case to
    PROPOSED. Shared by the operator path and, later, the agent path."""
    case_type = CaseType(case.case_type)
    try:
        expected_action = required_action(case_type, request.resolution_code)
    except ValueError as exc:
        raise InvalidProposalError(str(exc)) from exc
    if request.action_type != expected_action:
        raise InvalidProposalError(
            f"{request.resolution_code.value} requires action "
            f"{expected_action.value if expected_action else 'none'}"
        )
    params = _validated_params(request.action_type, request.action_params)

    transition(
        session,
        case,
        CaseState.PROPOSED,
        actor=proposer,
        occurred_at=occurred_at,
        reason="proposal_created",
    )
    action = request.action_type.value if request.action_type else None
    proposal = Proposal(
        id=new_id("prop"),
        tenant_id=case.tenant_id,
        case_id=case.id,
        case_version=case.version,
        proposer_type=proposer.type.value,
        proposer_id=proposer.id,
        resolution_code=request.resolution_code.value,
        action_type=action,
        action_params=params,
        risk_class=ACTION_RISK[request.action_type].value if request.action_type else None,
        reason_codes=request.reason_codes,
        evidence_refs=request.evidence_refs,
        rationale=request.rationale,
        content_hash=content_hash(
            case_id=case.id,
            resolution_code=request.resolution_code.value,
            action_type=action,
            action_params=params,
            evidence_refs=request.evidence_refs,
        ),
        status=ProposalStatus.PENDING.value,
        created_at=occurred_at,
    )
    session.add(proposal)
    session.flush()
    record_audit(
        session,
        tenant_id=case.tenant_id,
        actor=proposer,
        subject_type="case",
        subject_id=case.id,
        action="proposal.created",
        occurred_at=occurred_at,
        details={
            "proposal_id": proposal.id,
            "resolution_code": proposal.resolution_code,
            "action_type": proposal.action_type,
            "risk_class": proposal.risk_class,
            "content_hash": proposal.content_hash,
        },
    )
    return proposal


def _validated_params(action: ActionType | None, raw: dict[str, object]) -> dict[str, object]:
    if action is None:
        if raw:
            raise InvalidProposalError("a resolution without an action takes no parameters")
        return {}
    try:
        return ACTION_PARAMS[action].model_validate(raw).model_dump(mode="json")
    except ValidationError as exc:
        raise InvalidProposalError(f"invalid {action.value} parameters") from exc


class ProposalService:
    def __init__(self, sessions: sessionmaker[Session], clock: Clock = _utcnow) -> None:
        self._sessions = sessions
        self._clock = clock

    def propose(
        self,
        principal: Principal,
        case_id: str,
        request: ProposalRequest,
        *,
        expected_version: int,
    ) -> Proposal:
        principal.require(Permission.ACTION_PROPOSE)
        now = self._clock()
        actor = Actor.user(principal.user_id)
        with self._sessions() as session, session.begin():
            case = get_case(session, principal.tenant_id, case_id)
            if case.version != expected_version:
                raise StaleCaseError(case_id)
            if case.state == CaseState.ESCALATED:
                principal.require(Permission.CASE_WORK_ESCALATED)
            proposal = create_proposal(session, case, request, proposer=actor, occurred_at=now)
            self._accept(session, case, proposal, actor, now, note=None)
            return proposal

    def accept(
        self, principal: Principal, proposal_id: str, *, note: str | None = None
    ) -> Proposal:
        principal.require(Permission.PROPOSAL_DECIDE)
        now = self._clock()
        with self._sessions() as session, session.begin():
            proposal, case = self._pending(session, principal.tenant_id, proposal_id)
            self._accept(session, case, proposal, Actor.user(principal.user_id), now, note)
            return proposal

    def reject(self, principal: Principal, proposal_id: str, *, note: str) -> Proposal:
        principal.require(Permission.PROPOSAL_DECIDE)
        now = self._clock()
        actor = Actor.user(principal.user_id)
        with self._sessions() as session, session.begin():
            proposal, case = self._pending(session, principal.tenant_id, proposal_id)
            _decide(proposal, ProposalStatus.REJECTED, actor, now, note)
            transition(
                session,
                case,
                CaseState.INVESTIGATING,
                actor=actor,
                occurred_at=now,
                reason="proposal_rejected",
                details={"proposal_id": proposal.id, "note": note},
            )
            return proposal

    def _pending(
        self, session: Session, tenant_id: str, proposal_id: str
    ) -> tuple[Proposal, SupportCase]:
        proposal = session.scalar(
            select(Proposal).filter_by(tenant_id=tenant_id, id=proposal_id).with_for_update()
        )
        if proposal is None:
            raise ProposalNotFoundError(proposal_id)
        if proposal.status != ProposalStatus.PENDING:
            raise ProposalNotPendingError(proposal_id)
        return proposal, get_case(session, tenant_id, proposal.case_id)

    def _accept(
        self,
        session: Session,
        case: SupportCase,
        proposal: Proposal,
        actor: Actor,
        now: datetime,
        note: str | None,
    ) -> None:
        _decide(proposal, ProposalStatus.ACCEPTED, actor, now, note)
        if proposal.action_type is None:
            case.resolution_code = proposal.resolution_code
            transition(
                session,
                case,
                CaseState.RESOLVED,
                actor=actor,
                occurred_at=now,
                reason="resolution_accepted",
                details={"proposal_id": proposal.id, "resolution_code": proposal.resolution_code},
            )
            return

        if over_payout_limit(session, case.tenant_id, proposal):
            case.escalation_reason = EscalationReason.AMOUNT_OVER_LIMIT.value
            transition(
                session,
                case,
                CaseState.ESCALATED,
                actor=actor,
                occurred_at=now,
                reason=EscalationReason.AMOUNT_OVER_LIMIT.value,
                details={"proposal_id": proposal.id},
            )
            return

        transition(
            session,
            case,
            CaseState.AWAITING_APPROVAL,
            actor=actor,
            occurred_at=now,
            reason="approval_required",
            details={"proposal_id": proposal.id, "risk_class": proposal.risk_class},
        )


def _decide(
    proposal: Proposal, status: ProposalStatus, actor: Actor, now: datetime, note: str | None
) -> None:
    proposal.status = status.value
    proposal.decided_at = now
    proposal.decided_by_id = actor.id
    proposal.decision_note = note


def over_payout_limit(session: Session, tenant_id: str, proposal: Proposal) -> bool:
    if proposal.action_type != ActionType.PAYOUT_ADJUSTMENT:
        return False
    limit = session.get_one(Tenant, tenant_id).payout_approval_limit_cents
    amount = proposal.action_params["amount_cents"]
    return isinstance(amount, int) and amount > limit
