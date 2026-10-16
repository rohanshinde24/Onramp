from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from onramp.api.auth import CurrentPrincipal, Services
from onramp.cases.evidence import case_records
from onramp.cases.queries import get_case
from onramp.identity.roles import Permission

router = APIRouter(tags=["evidence"])


class EvidenceItem(BaseModel):
    """One marketplace record behind a case, under the reference proposals and checks cite.

    Document text and reviewer comments are worker- or reviewer-supplied and untrusted; the
    console shows them as quoted data.
    """

    ref: str
    kind: str
    source: Literal["marketplace"]
    record: dict[str, Any]


@router.get("/cases/{case_id}/evidence", response_model=list[EvidenceItem])
def list_evidence(
    case_id: str, principal: CurrentPrincipal, services: Services
) -> list[EvidenceItem]:
    principal.require(Permission.CASE_READ)
    with services.sessions() as session:
        case = get_case(session, principal.tenant_id, case_id)
        session.expunge(case)
    return [
        EvidenceItem(
            ref=record.ref,
            kind=record.kind,
            source="marketplace",
            record=record.model_dump(mode="json", exclude={"tenant_id"}),
        )
        for record in case_records(services.marketplace, case)
    ]
