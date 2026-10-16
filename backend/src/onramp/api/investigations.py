from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from onramp.agent.models import InvestigationRun
from onramp.api.auth import CurrentPrincipal, get_services
from onramp.api.services import AppServices
from onramp.cases.queries import get_case
from onramp.identity.roles import Permission

router = APIRouter(tags=["investigations"])
Services = Annotated[AppServices, Depends(get_services)]


class InvestigationRunView(BaseModel):
    """One investigation as stored: what the model concluded, what it did, and what it cost.

    The trajectory holds tool names, arguments and the references each call returned, never the
    records themselves, so worker-supplied text does not travel through this view.
    """

    id: str
    case_id: str
    case_version: int
    model_name: str
    prompt_version: str
    status: str
    conclusion: dict[str, Any] | None
    problems: list[str]
    model_calls: int
    input_tokens: int
    output_tokens: int
    elapsed_s: float
    trajectory: list[dict[str, Any]]
    proposal_id: str | None
    created_at: datetime


@router.get("/cases/{case_id}/investigations", response_model=list[InvestigationRunView])
def list_investigations(
    case_id: str, principal: CurrentPrincipal, services: Services
) -> list[InvestigationRunView]:
    principal.require(Permission.CASE_READ)
    with services.sessions() as session:
        get_case(session, principal.tenant_id, case_id)
        runs = session.scalars(
            select(InvestigationRun)
            .filter_by(tenant_id=principal.tenant_id, case_id=case_id)
            .order_by(InvestigationRun.created_at, InvestigationRun.id)
        )
        return [InvestigationRunView.model_validate(r, from_attributes=True) for r in runs]
