from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from onramp.api.auth import CurrentPrincipal, get_services
from onramp.api.services import AppServices
from onramp.cases.queries import get_case
from onramp.identity.roles import Permission
from onramp.proposals.content import ProposalRequest
from onramp.proposals.models import Proposal

router = APIRouter(tags=["proposals"])
Services = Annotated[AppServices, Depends(get_services)]


class ProposalView(BaseModel):
    id: str
    case_id: str
    proposer_type: str
    proposer_id: str | None
    resolution_code: str
    action_type: str | None
    action_params: dict[str, Any]
    risk_class: str | None
    reason_codes: list[str]
    evidence_refs: list[str]
    rationale: str
    content_hash: str
    status: str
    created_at: datetime
    decided_at: datetime | None
    decided_by_id: str | None
    decision_note: str | None

    @classmethod
    def of(cls, p: Proposal) -> "ProposalView":
        return cls.model_validate(p, from_attributes=True)


class ProposeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(gt=0)
    proposal: ProposalRequest


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(default="", max_length=2000)


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(min_length=1, max_length=2000)


@router.post(
    "/cases/{case_id}/proposals",
    response_model=ProposalView,
    status_code=status.HTTP_201_CREATED,
)
def propose(
    case_id: str, body: ProposeBody, principal: CurrentPrincipal, services: Services
) -> ProposalView:
    proposal = services.proposals.propose(
        principal, case_id, body.proposal, expected_version=body.expected_version
    )
    return ProposalView.of(proposal)


@router.get("/cases/{case_id}/proposals", response_model=list[ProposalView])
def list_proposals(
    case_id: str, principal: CurrentPrincipal, services: Services
) -> list[ProposalView]:
    principal.require(Permission.CASE_READ)
    with services.sessions() as session:
        get_case(session, principal.tenant_id, case_id)
        rows = session.scalars(
            select(Proposal)
            .filter_by(tenant_id=principal.tenant_id, case_id=case_id)
            .order_by(Proposal.created_at, Proposal.id)
        )
        return [ProposalView.of(p) for p in rows]


@router.post("/proposals/{proposal_id}/accept", response_model=ProposalView)
def accept(
    proposal_id: str, body: DecisionBody, principal: CurrentPrincipal, services: Services
) -> ProposalView:
    return ProposalView.of(
        services.proposals.accept(principal, proposal_id, note=body.note or None)
    )


@router.post("/proposals/{proposal_id}/reject", response_model=ProposalView)
def reject(
    proposal_id: str, body: RejectBody, principal: CurrentPrincipal, services: Services
) -> ProposalView:
    return ProposalView.of(services.proposals.reject(principal, proposal_id, note=body.note))
