from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from onramp.api.auth import CurrentPrincipal, get_services
from onramp.api.services import AppServices
from onramp.cases.queries import get_case
from onramp.identity.roles import Permission
from onramp.policy.applicability import applicable_versions_for_domain
from onramp.policy.models import PolicyPassage

router = APIRouter(tags=["policies"])
Services = Annotated[AppServices, Depends(get_services)]


class PassageView(BaseModel):
    passage_id: str
    policy_id: str
    version: int
    section: str
    heading: str
    text: str


class AppliedVersion(BaseModel):
    policy_id: str
    version: int
    effective_from: datetime
    effective_until: datetime | None
    parameters: dict[str, Any]


class CasePolicy(BaseModel):
    case_id: str
    incident_at: datetime
    incident_basis: str
    versions: list[AppliedVersion]


class PassageNotFoundError(LookupError):
    pass


@router.get("/policy-passages/{passage_id}", response_model=PassageView)
def get_passage(passage_id: str, principal: CurrentPrincipal, services: Services) -> PassageView:
    """Policy text is shared across tenants; any user who can read cases can read it."""
    principal.require(Permission.CASE_READ)
    with services.sessions() as session:
        passage = session.get(PolicyPassage, passage_id)
        if passage is None:
            raise PassageNotFoundError(passage_id)
        return PassageView.model_validate(passage, from_attributes=True)


@router.get("/cases/{case_id}/policy", response_model=CasePolicy)
def case_policy(case_id: str, principal: CurrentPrincipal, services: Services) -> CasePolicy:
    """The policy versions in force at the case's incident time, with their parameters."""
    principal.require(Permission.CASE_READ)
    with services.sessions() as session:
        case = get_case(session, principal.tenant_id, case_id)
        versions = applicable_versions_for_domain(session, case.case_type, case.incident_at)
        return CasePolicy(
            case_id=case.id,
            incident_at=case.incident_at,
            incident_basis=case.incident_basis,
            versions=[AppliedVersion.model_validate(v, from_attributes=True) for v in versions],
        )
