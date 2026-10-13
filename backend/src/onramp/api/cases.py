from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from onramp.api.auth import CurrentPrincipal, get_services
from onramp.api.services import AppServices
from onramp.cases import queries
from onramp.cases.escalation import EscalationReason
from onramp.cases.models import CaseType, SupportCase
from onramp.cases.service import IntakeRequest
from onramp.cases.state import CaseState
from onramp.identity.roles import Permission

router = APIRouter(prefix="/cases", tags=["cases"])
Services = Annotated[AppServices, Depends(get_services)]


class CaseView(BaseModel):
    id: str
    case_type: str
    state: str
    version: int
    subject: str
    worker_id: str
    incident_at: datetime
    incident_basis: str
    reported_description: str
    assignee_id: str | None
    resolution_code: str | None
    escalation_reason: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, case: SupportCase) -> "CaseView":
        return cls(
            id=case.id,
            case_type=case.case_type,
            state=case.state,
            version=case.version,
            subject=f"{case.subject_type}:{case.subject_id}",
            worker_id=case.worker_id,
            incident_at=case.incident_at,
            incident_basis=case.incident_basis,
            reported_description=case.reported_description,
            assignee_id=case.assignee_id,
            resolution_code=case.resolution_code,
            escalation_reason=case.escalation_reason,
            created_at=case.created_at,
            updated_at=case.updated_at,
        )


class IntakeResponse(BaseModel):
    case: CaseView
    created: bool
    attached_to_existing: bool


class CasePage(BaseModel):
    cases: list[CaseView]
    next_before: str | None


class TimelineEntry(BaseModel):
    event_id: str
    occurred_at: datetime
    actor_type: str
    actor_id: str | None
    action: str
    details: dict[str, Any]


class EscalateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: EscalationReason
    expected_version: int = Field(gt=0)
    note: str = Field(default="", max_length=2000)


@router.post("", response_model=IntakeResponse)
def open_case(
    body: IntakeRequest, principal: CurrentPrincipal, services: Services, response: Response
) -> IntakeResponse:
    result = services.cases.open_case(principal, body)
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return IntakeResponse(
        case=CaseView.of(result.case),
        created=result.created,
        attached_to_existing=result.attached_to_existing,
    )


@router.get("", response_model=CasePage)
def list_cases(
    principal: CurrentPrincipal,
    services: Services,
    state: Annotated[list[CaseState] | None, Query()] = None,
    case_type: CaseType | None = None,
    before: Annotated[str | None, Query(description="next_before from the previous page")] = None,
    limit: Annotated[int, Query(ge=1, le=queries.MAX_PAGE)] = 50,
) -> CasePage:
    principal.require(Permission.CASE_READ)
    cursor = _parse_cursor(before) if before else None
    with services.sessions() as session:
        cases = queries.list_cases(
            session,
            principal.tenant_id,
            states=state,
            case_type=case_type,
            before=cursor,
            limit=limit,
        )
    next_before = (
        f"{cases[-1].created_at.isoformat()}|{cases[-1].id}" if len(cases) == limit else None
    )
    return CasePage(cases=[CaseView.of(c) for c in cases], next_before=next_before)


@router.get("/{case_id}", response_model=CaseView)
def get_case(case_id: str, principal: CurrentPrincipal, services: Services) -> CaseView:
    principal.require(Permission.CASE_READ)
    with services.sessions() as session:
        return CaseView.of(queries.get_case(session, principal.tenant_id, case_id))


@router.get("/{case_id}/timeline", response_model=list[TimelineEntry])
def get_timeline(
    case_id: str, principal: CurrentPrincipal, services: Services
) -> list[TimelineEntry]:
    principal.require(Permission.CASE_READ)
    with services.sessions() as session:
        events = queries.case_timeline(session, principal.tenant_id, case_id)
    return [
        TimelineEntry(
            event_id=e.event_id,
            occurred_at=e.occurred_at,
            actor_type=e.actor_type,
            actor_id=e.actor_id,
            action=e.action,
            details=e.details,
        )
        for e in events
    ]


@router.post("/{case_id}/escalate", response_model=CaseView)
def escalate(
    case_id: str, body: EscalateRequest, principal: CurrentPrincipal, services: Services
) -> CaseView:
    case = services.cases.escalate(
        principal, case_id, body.reason, expected_version=body.expected_version, note=body.note
    )
    return CaseView.of(case)


class CloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(gt=0)
    note: str = Field(min_length=1, max_length=2000)


@router.post("/{case_id}/close", response_model=CaseView)
def close(
    case_id: str, body: CloseRequest, principal: CurrentPrincipal, services: Services
) -> CaseView:
    case = services.cases.close_without_action(
        principal, case_id, expected_version=body.expected_version, note=body.note
    )
    return CaseView.of(case)


class InvalidCursorError(ValueError):
    pass


def _parse_cursor(raw: str) -> tuple[datetime, str]:
    try:
        created_at, case_id = raw.split("|", 1)
        return datetime.fromisoformat(created_at), case_id
    except ValueError as exc:
        raise InvalidCursorError(raw) from exc
