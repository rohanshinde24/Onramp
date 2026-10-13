from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.events.contracts import CaseEscalatedV1, CaseResolvedV1
from onramp.events.envelope import EventEnvelope
from onramp.events.models import OutboxEvent
from onramp.events.outbox import ContractMismatchError, emit
from onramp.telemetry.logging import log_context

NOW = datetime(2026, 8, 21, tzinfo=UTC)


def _escalated(
    session: Session, case_id: str = "case_1", correlation_id: str | None = None
) -> EventEnvelope:
    return emit(
        session,
        event_type="case.escalated",
        tenant_id="acme",
        aggregate_id=case_id,
        payload=CaseEscalatedV1(reason="operator_judgment", from_state="investigating"),
        occurred_at=NOW,
        correlation_id=correlation_id,
    )


def _rows(sessions: sessionmaker[Session]) -> list[OutboxEvent]:
    with sessions() as session:
        return list(session.scalars(select(OutboxEvent).order_by(OutboxEvent.id)))


def test_emit_stores_full_envelope_with_topic_and_key(
    onramp_sessions: sessionmaker[Session],
) -> None:
    with onramp_sessions() as session, session.begin():
        envelope = _escalated(session, correlation_id="corr_9")

    (row,) = _rows(onramp_sessions)
    assert row.topic == "onramp.cases"
    assert row.partition_key == "case_1"
    assert row.published_at is None
    assert EventEnvelope.model_validate(row.envelope) == envelope
    assert envelope.correlation_id == "corr_9"


def test_rolled_back_transaction_leaves_no_event(onramp_sessions: sessionmaker[Session]) -> None:
    def change_that_fails() -> None:
        with onramp_sessions() as session, session.begin():
            _escalated(session)
            raise RuntimeError("business change failed")

    with pytest.raises(RuntimeError):
        change_that_fails()

    assert _rows(onramp_sessions) == []


def test_versions_increase_per_aggregate(onramp_sessions: sessionmaker[Session]) -> None:
    with onramp_sessions() as session, session.begin():
        a1 = _escalated(session, "case_a")
        b1 = _escalated(session, "case_b")
        a2 = _escalated(session, "case_a")

    assert (a1.aggregate_version, a2.aggregate_version, b1.aggregate_version) == (1, 2, 1)


def test_correlation_defaults_to_request_id(onramp_sessions: sessionmaker[Session]) -> None:
    with log_context(request_id="req_7"), onramp_sessions() as session, session.begin():
        envelope = _escalated(session)

    assert envelope.correlation_id == "req_7"


def test_payload_must_match_the_event_contract(onramp_sessions: sessionmaker[Session]) -> None:
    with pytest.raises(ContractMismatchError), onramp_sessions() as session, session.begin():
        emit(
            session,
            event_type="case.escalated",
            tenant_id="acme",
            aggregate_id="case_1",
            payload=CaseResolvedV1(resolution_code="PAID_IN_FULL"),
            occurred_at=NOW,
        )


def test_concurrent_emitters_get_distinct_versions(
    onramp_committed: sessionmaker[Session],
) -> None:
    def one(_: int) -> int:
        with onramp_committed() as session, session.begin():
            return _escalated(session, "case_hot").aggregate_version

    with ThreadPoolExecutor(max_workers=8) as pool:
        versions = sorted(pool.map(one, range(20)))

    assert versions == list(range(1, 21))
