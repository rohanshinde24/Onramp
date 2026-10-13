from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from onramp.api.auth import CurrentPrincipal, get_services
from onramp.api.services import AppServices
from onramp.approvals.models import Approval, Decision

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
    return ApprovalView.of(approval)
