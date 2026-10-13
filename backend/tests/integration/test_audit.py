from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from onramp.audit.models import ActorType, AuditEvent
from onramp.audit.service import Actor, record_audit
from onramp.identity.models import Tenant

NOW = datetime(2026, 8, 21, tzinfo=UTC)


@pytest.fixture
def event(onramp_session: Session) -> AuditEvent:
    onramp_session.add(Tenant(id="acme", name="Acme", payout_approval_limit_cents=50_000))
    onramp_session.flush()
    event = record_audit(
        onramp_session,
        tenant_id="acme",
        actor=Actor.user("usr_1"),
        subject_type="case",
        subject_id="case_1",
        action="case.created",
        occurred_at=NOW,
        details={"case_type": "payout"},
    )
    onramp_session.flush()
    return event


def test_audit_event_is_recorded(onramp_session: Session, event: AuditEvent) -> None:
    stored = onramp_session.scalars(select(AuditEvent)).one()

    assert stored.event_id.startswith("aud_")
    assert stored.actor_type == ActorType.USER
    assert stored.details == {"case_type": "payout"}


def test_update_is_rejected_by_database(onramp_session: Session, event: AuditEvent) -> None:
    with pytest.raises(IntegrityError, match="append-only"):
        onramp_session.execute(update(AuditEvent).values(action="case.deleted"))


def test_delete_is_rejected_by_database(onramp_session: Session, event: AuditEvent) -> None:
    with pytest.raises(IntegrityError, match="append-only"):
        onramp_session.execute(delete(AuditEvent))


def test_user_actor_requires_an_id(onramp_session: Session, event: AuditEvent) -> None:
    record_audit(
        onramp_session,
        tenant_id="acme",
        actor=Actor(ActorType.USER, None),
        subject_type="case",
        subject_id="case_1",
        action="case.viewed",
        occurred_at=NOW,
    )

    with pytest.raises(IntegrityError, match="ck_audit_event_actor_id_matches_type"):
        onramp_session.flush()
