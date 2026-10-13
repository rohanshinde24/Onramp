from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.events.contracts import parse_payload
from onramp.events.envelope import EventEnvelope
from onramp.marketplace.activity import MarketplaceActivity
from onramp.marketplace.builder import WorldBuilder
from onramp.marketplace.models import MarketplaceOutboxEvent
from onramp.marketplace.writer import RejectedError, SqlMarketplaceWriter

T0 = datetime(2026, 8, 20, 9, tzinfo=UTC)


@pytest.fixture
def sessions(marketplace_sessions: sessionmaker[Session]) -> sessionmaker[Session]:
    with marketplace_sessions() as session, session.begin():
        w = WorldBuilder(session, "acme").tenant()
        w.worker("w_1", "Sam Lee")
        w.worker("w_new", "Ana Diaz", status="pending_onboarding")
        w.task("t_1", 8400)
        w.assignment("a_1", "t_1", "w_1", T0)
        w.submission("s_1", "a_1", T0)
        w.review("qr_1", "s_1", "fail", "0.4", T0)
        w.payment("pay_1", "w_1", 8400, "authorized", T0, submission_id="s_1")
    return marketplace_sessions


def _events(sessions: sessionmaker[Session]) -> list[EventEnvelope]:
    with sessions() as session:
        rows = session.scalars(select(MarketplaceOutboxEvent).order_by(MarketplaceOutboxEvent.id))
        return [EventEnvelope.model_validate(r.envelope) for r in rows]


def test_adjustment_publishes_authorized_and_settled_once(
    sessions: sessionmaker[Session],
) -> None:
    writer = SqlMarketplaceWriter(sessions, clock=lambda: T0)
    payment = writer.issue_adjustment("acme", "w_1", 8400, "USD", idempotency_key="k")
    writer.issue_adjustment("acme", "w_1", 8400, "USD", idempotency_key="k")

    events = _events(sessions)
    assert [e.event_type for e in events] == ["payment.authorized", "payment.settled"]
    assert {e.aggregate_id for e in events} == {payment.id}
    assert [e.aggregate_version for e in events] == [1, 2]


def test_activation_and_overturn_publish_their_events(sessions: sessionmaker[Session]) -> None:
    writer = SqlMarketplaceWriter(sessions, clock=lambda: T0)
    writer.activate_worker("acme", "w_new", idempotency_key="a")
    writer.overturn_review("acme", "qr_1", idempotency_key="b")

    worker_event, review_event = _events(sessions)
    assert (worker_event.event_type, worker_event.payload["status"]) == ("worker.updated", "active")
    assert review_event.event_type == "quality_review.overturned"
    assert review_event.aggregate_id == "s_1"  # keyed by submission on marketplace.work


def test_uploaded_document_publishes_evidence_event(sessions: sessionmaker[Session]) -> None:
    document_id = MarketplaceActivity(sessions, clock=lambda: T0).upload_document(
        "acme", "w_new", "tax_form", "W-9 attached"
    )

    (event,) = _events(sessions)
    payload = parse_payload(event)
    assert event.event_type == "evidence.uploaded"
    assert payload.model_dump() == {
        "document_id": document_id,
        "worker_id": "w_new",
        "document_kind": "tax_form",
    }


def test_delayed_settlement_failure_publishes_once(sessions: sessionmaker[Session]) -> None:
    activity = MarketplaceActivity(sessions, clock=lambda: T0)
    activity.fail_settlement("acme", "pay_1", "bank_account_closed")

    with pytest.raises(RejectedError):
        activity.settle_payment("acme", "pay_1")
    (event,) = _events(sessions)
    assert event.event_type == "payment.settlement_failed"
    assert event.payload["failure_reason"] == "bank_account_closed"
