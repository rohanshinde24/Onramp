from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from onramp.cases.models import SupportCase
from onramp.entrypoints.seed import seed

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def test_seed_opens_cases_through_intake_and_is_idempotent(
    onramp_sessions: sessionmaker[Session], marketplace_sessions: sessionmaker[Session]
) -> None:
    assert seed(onramp_sessions, marketplace_sessions, NOW) == ["acme", "globex"]
    assert seed(onramp_sessions, marketplace_sessions, NOW) == []

    with onramp_sessions() as session:
        per_tenant = dict(
            session.execute(
                select(SupportCase.tenant_id, func.count()).group_by(SupportCase.tenant_id)
            ).all()
        )
        payout = session.scalar(
            select(SupportCase).filter_by(tenant_id="acme", subject_id="acme_s_8821")
        )
    assert per_tenant == {"acme": 4, "globex": 4}
    assert payout is not None
    assert payout.worker_id == "acme_w_492"
