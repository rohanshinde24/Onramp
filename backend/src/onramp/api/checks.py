from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from onramp.api.auth import CurrentPrincipal, get_services
from onramp.api.services import AppServices
from onramp.api.workflow_notifications import signal_case
from onramp.audit.service import Actor
from onramp.cases.queries import get_case
from onramp.checks.models import CheckRun
from onramp.identity.roles import Permission

router = APIRouter(tags=["checks"])
Services = Annotated[AppServices, Depends(get_services)]


class CheckRunView(BaseModel):
    id: str
    case_id: str
    case_version: int
    policy_id: str | None
    policy_version: int | None
    outcome: str
    reason_codes: list[str]
    evidence_refs: list[str]
    citations: list[str]
    findings: list[str]
    missing_items: list[str]
    wait_until: datetime | None
    error: str | None
    proposal_id: str | None
    created_at: datetime

    @classmethod
    def of(cls, run: CheckRun) -> "CheckRunView":
        return cls.model_validate(run, from_attributes=True)


class RunChecksBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(gt=0)


@router.post(
    "/cases/{case_id}/checks", response_model=CheckRunView, status_code=status.HTTP_201_CREATED
)
def run_checks(
    case_id: str, body: RunChecksBody, principal: CurrentPrincipal, services: Services
) -> CheckRunView:
    principal.require(Permission.CASE_INVESTIGATE)
    run = services.checks.run(
        principal.tenant_id,
        case_id,
        Actor.user(principal.user_id),
        expected_version=body.expected_version,
    )
    signal_case(
        services.workflows,
        principal.tenant_id,
        case_id,
        "case_changed",
        f"direct:check:{run.id}",
    )
    return CheckRunView.of(run)


@router.get("/cases/{case_id}/checks", response_model=list[CheckRunView])
def list_check_runs(
    case_id: str, principal: CurrentPrincipal, services: Services
) -> list[CheckRunView]:
    principal.require(Permission.CASE_READ)
    with services.sessions() as session:
        get_case(session, principal.tenant_id, case_id)
        runs = session.scalars(
            select(CheckRun)
            .filter_by(tenant_id=principal.tenant_id, case_id=case_id)
            .order_by(CheckRun.created_at, CheckRun.id)
        )
        return [CheckRunView.of(r) for r in runs]
