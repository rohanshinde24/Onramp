from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from onramp.marketplace.models import (
    Assignment,
    MarketplaceTenant,
    Payment,
    Submission,
    Task,
    Worker,
)

NOW = datetime(2026, 8, 21, 12, tzinfo=UTC)


def _world(session: Session, tenant_id: str) -> None:
    session.add(MarketplaceTenant(id=tenant_id, name=tenant_id))
    session.flush()
    session.add_all(
        [
            Worker(
                id=f"{tenant_id}-w",
                tenant_id=tenant_id,
                full_name="Sam Lee",
                email="sam@example.test",
                status="active",
                country="US",
            ),
            Task(
                id=f"{tenant_id}-t",
                tenant_id=tenant_id,
                title="Label images",
                task_type="labeling",
                payout_cents=8400,
                currency="USD",
            ),
        ]
    )
    session.flush()


def test_assignment_cannot_reference_another_tenants_worker(
    marketplace_session: Session,
) -> None:
    _world(marketplace_session, "a")
    _world(marketplace_session, "b")
    marketplace_session.add(
        Assignment(
            id="asg",
            tenant_id="a",
            task_id="a-t",
            worker_id="b-w",
            status="assigned",
            assigned_at=NOW,
        )
    )

    with pytest.raises(IntegrityError, match="fk_assignment_tenant_id_worker"):
        marketplace_session.flush()


def test_namesakes_are_distinct_workers(marketplace_session: Session) -> None:
    _world(marketplace_session, "a")
    marketplace_session.add(
        Worker(
            id="a-w2",
            tenant_id="a",
            full_name="Sam Lee",
            email="other@example.test",
            status="active",
            country="US",
        )
    )

    marketplace_session.flush()


def test_task_payout_requires_a_submission(marketplace_session: Session) -> None:
    _world(marketplace_session, "a")
    marketplace_session.add(
        Payment(
            id="pay",
            tenant_id="a",
            worker_id="a-w",
            submission_id=None,
            kind="task_payout",
            amount_cents=8400,
            currency="USD",
            status="authorized",
            created_at=NOW,
        )
    )

    with pytest.raises(IntegrityError, match="ck_payment_task_payout_has_submission"):
        marketplace_session.flush()


def test_adjustment_has_no_submission(marketplace_session: Session) -> None:
    _world(marketplace_session, "a")
    marketplace_session.add(
        Assignment(
            id="asg",
            tenant_id="a",
            task_id="a-t",
            worker_id="a-w",
            status="submitted",
            assigned_at=NOW,
        )
    )
    marketplace_session.flush()
    marketplace_session.add(
        Submission(
            id="sub", tenant_id="a", assignment_id="asg", status="accepted", submitted_at=NOW
        )
    )
    marketplace_session.flush()
    marketplace_session.add(
        Payment(
            id="pay",
            tenant_id="a",
            worker_id="a-w",
            submission_id="sub",
            kind="adjustment",
            amount_cents=100,
            currency="USD",
            status="authorized",
            created_at=NOW,
        )
    )

    with pytest.raises(IntegrityError, match="ck_payment_task_payout_has_submission"):
        marketplace_session.flush()
