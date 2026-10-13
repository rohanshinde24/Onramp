"""Recovery for cases committed before their Temporal workflow was started."""

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.cases.models import SupportCase
from onramp.cases.state import TERMINAL_STATES
from onramp.workflows.client import CaseCoordinator


def reconcile_case_workflows(sessions: sessionmaker[Session], coordinator: CaseCoordinator) -> int:
    with sessions() as session:
        cases = session.execute(
            select(SupportCase.tenant_id, SupportCase.id)
            .where(SupportCase.state.not_in([state.value for state in TERMINAL_STATES]))
            .order_by(SupportCase.id)
        ).all()
    for tenant_id, case_id in cases:
        coordinator.start_case(tenant_id, case_id)
    return len(cases)
