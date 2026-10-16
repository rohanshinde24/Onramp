from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from onramp.actions.models import ExecutedAction
from onramp.api.auth import CurrentPrincipal, get_services
from onramp.api.services import AppServices
from onramp.cases.queries import get_case
from onramp.identity.roles import Permission

router = APIRouter(tags=["actions"])
Services = Annotated[AppServices, Depends(get_services)]


class ExecutedActionView(BaseModel):
    """The gateway's durable record of one approved action: what it sent and what it verified."""

    id: str
    case_id: str
    proposal_id: str
    approval_id: str
    action_type: str
    idempotency_key: str
    request: dict[str, Any]
    status: str
    result: dict[str, Any] | None
    failure_code: str | None
    failure_message: str | None
    created_at: datetime
    completed_at: datetime | None


@router.get("/cases/{case_id}/actions", response_model=list[ExecutedActionView])
def list_actions(
    case_id: str, principal: CurrentPrincipal, services: Services
) -> list[ExecutedActionView]:
    principal.require(Permission.CASE_READ)
    with services.sessions() as session:
        get_case(session, principal.tenant_id, case_id)
        actions = session.scalars(
            select(ExecutedAction)
            .filter_by(tenant_id=principal.tenant_id, case_id=case_id)
            .order_by(ExecutedAction.created_at, ExecutedAction.id)
        )
        return [ExecutedActionView.model_validate(a, from_attributes=True) for a in actions]
