from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from onramp.api.auth import CurrentPrincipal, get_services
from onramp.api.services import AppServices
from onramp.api.workflow_notifications import signal_case
from onramp.approvals.models import Approval, Decision
from onramp.cases.queries import get_case
from onramp.events.models import OutboxEvent
from onramp.identity.roles import Permission
from onramp.proposals.models import Proposal

router = APIRouter(tags=["approvals"])
Services = Annotated[AppServices, Depends(get_services)]


class ApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Decision
    # The content hash shown to the approver with the proposal.
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    note: str = Field(default="", max_length=2000)


class ApprovalView(BaseModel):
    id: str
    proposal_id: str
    approver_id: str
    decision: str
    risk_class: str
    content_hash: str
    note: str
    decided_at: datetime

    @classmethod
    def of(cls, a: Approval) -> "ApprovalView":
        return cls.model_validate(a, from_attributes=True)


@router.post(
    "/proposals/{proposal_id}/approval",
    response_model=ApprovalView,
    status_code=status.HTTP_201_CREATED,
)
def decide(
    proposal_id: str, body: ApprovalBody, principal: CurrentPrincipal, services: Services
) -> ApprovalView:
    approval = services.approvals.decide(
        principal, proposal_id, body.decision, content_hash=body.content_hash, note=body.note
    )
    with services.sessions() as session:
        case_id = session.scalar(
            select(Proposal.case_id).filter_by(
                tenant_id=principal.tenant_id, id=approval.proposal_id
            )
        )
        event_id = session.scalar(
            select(OutboxEvent.event_id).where(
                OutboxEvent.tenant_id == principal.tenant_id,
                OutboxEvent.aggregate_id == case_id,
                OutboxEvent.event_type
                == (
                    "approval.granted"
                    if approval.decision == Decision.APPROVED
                    else "approval.rejected"
                ),
                OutboxEvent.envelope["payload"]["approval_id"].as_string() == approval.id,
            )
        )
    if case_id is not None and event_id is not None:
        signal_case(
            services.workflows,
            principal.tenant_id,
            case_id,
            "approval_decided",
            event_id,
        )
    return ApprovalView.of(approval)


@router.get("/cases/{case_id}/approvals", response_model=list[ApprovalView])
def list_approvals(
    case_id: str, principal: CurrentPrincipal, services: Services
) -> list[ApprovalView]:
    principal.require(Permission.CASE_READ)
    with services.sessions() as session:
        get_case(session, principal.tenant_id, case_id)
        rows = session.scalars(
            select(Approval)
            .join(
                Proposal,
                (Proposal.tenant_id == Approval.tenant_id) & (Proposal.id == Approval.proposal_id),
            )
            .where(Approval.tenant_id == principal.tenant_id, Proposal.case_id == case_id)
            .order_by(Approval.decided_at, Approval.id)
        )
        return [ApprovalView.of(a) for a in rows]
