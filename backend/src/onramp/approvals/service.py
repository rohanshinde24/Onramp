"""Human approval of proposed actions (PRODUCT_SPEC section 6, rules 4 and 7).

An approval is valid only if: the case is awaiting approval, the approver holds the
permission for the proposal's risk class, the approver did not propose it, and the approver
saw exactly the proposal's current content (content hash).
"""

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from onramp.approvals.models import Approval, Decision
from onramp.audit.service import Actor, record_audit
from onramp.cases.escalation import EscalationReason
from onramp.cases.queries import get_case
from onramp.cases.resolutions import RiskClass
from onramp.cases.service import transition
from onramp.cases.state import CaseState
from onramp.db.types import new_id
from onramp.events.contracts import ApprovalGrantedV1, ApprovalRejectedV1
from onramp.events.outbox import emit
from onramp.identity.principal import Principal
from onramp.identity.roles import Permission
from onramp.proposals.models import Proposal, ProposalStatus
from onramp.proposals.service import ProposalNotFoundError, over_payout_limit
from onramp.telemetry.metrics import approval_decisions, record_on_commit

Clock = Callable[[], datetime]

_REQUIRED_PERMISSION = {
    RiskClass.MEDIUM: Permission.APPROVE_MEDIUM_RISK,
    RiskClass.HIGH: Permission.APPROVE_HIGH_RISK,
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ApprovalError(Exception):
    pass


class NotAwaitingApprovalError(ApprovalError):
    pass


class SelfApprovalError(ApprovalError):
    pass


class ContentMismatchError(ApprovalError):
    """The approver saw different content from what would be executed."""


class AlreadyDecidedError(ApprovalError):
    pass


class ApprovalService:
    def __init__(self, sessions: sessionmaker[Session], clock: Clock = _utcnow) -> None:
        self._sessions = sessions
        self._clock = clock

    def decide(
        self,
        principal: Principal,
        proposal_id: str,
        decision: Decision,
        *,
        content_hash: str,
        note: str,
    ) -> Approval:
        try:
            return self._decide(principal, proposal_id, decision, content_hash, note)
        except IntegrityError as exc:
            if "uq_approval_tenant_id_proposal_id" in str(exc.orig):
                raise AlreadyDecidedError(proposal_id) from None
            raise

    def _decide(
        self,
        principal: Principal,
        proposal_id: str,
        decision: Decision,
        content_hash: str,
        note: str,
    ) -> Approval:
        now = self._clock()
        actor = Actor.user(principal.user_id)
        with self._sessions() as session, session.begin():
            proposal = session.scalar(
                select(Proposal)
                .filter_by(tenant_id=principal.tenant_id, id=proposal_id)
                .with_for_update()
            )
            if proposal is None:
                raise ProposalNotFoundError(proposal_id)
            action_type = proposal.action_type
            if (
                action_type is None
                or proposal.risk_class is None
                or proposal.status != ProposalStatus.ACCEPTED
            ):
                raise NotAwaitingApprovalError(proposal_id)
            risk = RiskClass(proposal.risk_class)
            principal.require(_REQUIRED_PERMISSION[risk])
            if proposal.proposer_type == "user" and proposal.proposer_id == principal.user_id:
                raise SelfApprovalError(proposal_id)
            if content_hash != proposal.content_hash:
                raise ContentMismatchError(proposal_id)

            case = get_case(session, principal.tenant_id, proposal.case_id)
            if case.state != CaseState.AWAITING_APPROVAL:
                # Already decided cases fall through to the unique constraint below when a
                # decision exists; anything else is simply not awaiting approval.
                existing = session.scalar(
                    select(Approval).filter_by(tenant_id=case.tenant_id, proposal_id=proposal.id)
                )
                if existing is not None:
                    raise AlreadyDecidedError(proposal_id)
                raise NotAwaitingApprovalError(proposal_id)

            approval = Approval(
                id=new_id("appr"),
                tenant_id=case.tenant_id,
                proposal_id=proposal.id,
                approver_id=principal.user_id,
                decision=decision.value,
                content_hash=content_hash,
                risk_class=risk.value,
                note=note,
                decided_at=now,
            )
            session.add(approval)
            session.flush()
            record_on_commit(
                session,
                approval_decisions,
                1,
                {"decision": decision.value, "risk_class": risk.value, "action_type": action_type},
            )
            record_audit(
                session,
                tenant_id=case.tenant_id,
                actor=actor,
                subject_type="case",
                subject_id=case.id,
                action=f"approval.{decision.value}",
                occurred_at=now,
                details={
                    "approval_id": approval.id,
                    "proposal_id": proposal.id,
                    "risk_class": risk.value,
                    "content_hash": content_hash,
                },
            )

            if decision is Decision.APPROVED:
                emit(
                    session,
                    event_type="approval.granted",
                    tenant_id=case.tenant_id,
                    aggregate_id=case.id,
                    payload=ApprovalGrantedV1(
                        proposal_id=proposal.id,
                        approval_id=approval.id,
                        action_type=action_type,
                        risk_class=risk.value,
                        content_hash=content_hash,
                    ),
                    occurred_at=now,
                )
            else:
                emit(
                    session,
                    event_type="approval.rejected",
                    tenant_id=case.tenant_id,
                    aggregate_id=case.id,
                    payload=ApprovalRejectedV1(proposal_id=proposal.id, approval_id=approval.id),
                    occurred_at=now,
                )

            if decision is Decision.REJECTED:
                transition(
                    session,
                    case,
                    CaseState.INVESTIGATING,
                    actor=actor,
                    occurred_at=now,
                    reason="approval_rejected",
                    details={"proposal_id": proposal.id, "note": note},
                )
            elif over_payout_limit(session, case.tenant_id, proposal):
                # The limit may have been lowered since the proposal was accepted.
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
            else:
                transition(
                    session,
                    case,
                    CaseState.EXECUTING,
                    actor=actor,
                    occurred_at=now,
                    reason="approved",
                    details={"proposal_id": proposal.id, "approval_id": approval.id},
                )
            return approval
