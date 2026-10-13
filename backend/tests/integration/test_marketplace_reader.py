from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from onramp.marketplace.builder import WorldBuilder
from onramp.marketplace.reader import SqlMarketplaceReader

T0 = datetime(2026, 8, 20, 9, tzinfo=UTC)


@pytest.fixture
def reader(marketplace_sessions: sessionmaker[Session]) -> SqlMarketplaceReader:
    with marketplace_sessions() as session, session.begin():
        acme = WorldBuilder(session, "acme").tenant()
        acme.worker("w_492", "Sam Lee")
        acme.task("t_8821", 8400)
        acme.assignment("a_1", "t_8821", "w_492", T0)
        acme.submission("s_1", "a_1", T0 + timedelta(hours=2))
        acme.review("qr_4281", "s_1", "pass", "0.92", T0 + timedelta(hours=5))
        acme.payment(
            "pay_9202", "w_492", 8400, "authorized", T0 + timedelta(hours=6), submission_id="s_1"
        )
        acme.payment_event(
            "pe_2",
            "pay_9202",
            "settlement_failed",
            8400,
            T0 + timedelta(days=2),
            failure_reason="bank_rejected",
        )
        acme.payment_event("pe_1", "pay_9202", "authorized", 8400, T0 + timedelta(hours=6))

        other = WorldBuilder(session, "globex").tenant()
        other.worker("w_999", "Sam Lee")
    return SqlMarketplaceReader(marketplace_sessions)


def test_reads_payout_evidence_chain(reader: SqlMarketplaceReader) -> None:
    submission = reader.get_submission("acme", "s_1")
    assert submission is not None
    payments = reader.get_payments_for_submission("acme", submission.id)

    assert [p.ref for p in payments] == ["payment:pay_9202"]
    events = reader.get_payment_events("acme", "pay_9202")
    assert [e.event_type for e in events] == ["authorized", "settlement_failed"]
    assert events[1].failure_reason == "bank_rejected"


def test_review_and_task_records_carry_refs(reader: SqlMarketplaceReader) -> None:
    (review,) = reader.get_quality_reviews("acme", "s_1")
    task = reader.get_task("acme", "t_8821")

    assert review.ref == "quality_review:qr_4281"
    assert task is not None
    assert task.payout_cents == 8400


def test_other_tenant_record_reads_as_missing(reader: SqlMarketplaceReader) -> None:
    assert reader.get_worker("acme", "w_999") is None
    assert reader.get_worker("globex", "w_492") is None
    assert reader.get_payments_for_worker("globex", "w_492") == []


def test_records_are_immutable(reader: SqlMarketplaceReader) -> None:
    worker = reader.get_worker("acme", "w_492")
    assert worker is not None

    with pytest.raises(ValueError, match="frozen"):
        worker.status = "suspended"  # type: ignore[misc]
