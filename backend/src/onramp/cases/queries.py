"""Read side for the operator console. Every query is bounded to one tenant."""

from datetime import datetime

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from onramp.audit.models import AuditEvent
from onramp.cases.models import CaseType, SupportCase
from onramp.cases.state import CaseState

MAX_PAGE = 100


class CaseNotFoundError(Exception):
    """No such case in the caller's tenant. Same error whether it exists elsewhere or not."""


def get_case(session: Session, tenant_id: str, case_id: str) -> SupportCase:
    case = session.scalar(select(SupportCase).filter_by(tenant_id=tenant_id, id=case_id))
    if case is None:
        raise CaseNotFoundError(case_id)
    return case


def list_cases(
    session: Session,
    tenant_id: str,
    *,
    states: list[CaseState] | None = None,
    case_type: CaseType | None = None,
    before: tuple[datetime, str] | None = None,
    limit: int = 50,
) -> list[SupportCase]:
    """Newest first, keyset-paginated on (created_at, id)."""
    query = select(SupportCase).where(SupportCase.tenant_id == tenant_id)
    if states:
        query = query.where(SupportCase.state.in_([s.value for s in states]))
    if case_type is not None:
        query = query.where(SupportCase.case_type == case_type.value)
    if before is not None:
        query = query.where(tuple_(SupportCase.created_at, SupportCase.id) < before)
    query = query.order_by(SupportCase.created_at.desc(), SupportCase.id.desc())
    return list(session.scalars(query.limit(min(limit, MAX_PAGE))))


def case_timeline(session: Session, tenant_id: str, case_id: str) -> list[AuditEvent]:
    get_case(session, tenant_id, case_id)
    return list(
        session.scalars(
            select(AuditEvent)
            .filter_by(tenant_id=tenant_id, subject_type="case", subject_id=case_id)
            .order_by(AuditEvent.id)
        )
    )
