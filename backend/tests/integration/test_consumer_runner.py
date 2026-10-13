"""Consumer runner semantics with an in-memory source, so every failure point is controllable.
The handler's effect is an audit row, which makes "applied exactly once" countable."""

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from onramp.audit.models import AuditEvent
from onramp.audit.service import Actor, record_audit
from onramp.events.consumer import ConsumerRunner, Outcome, RawMessage
from onramp.events.contracts import CaseEscalatedV1, serialize_payload
from onramp.events.envelope import EventEnvelope
from onramp.events.models import ProcessedEvent
from onramp.identity.models import Tenant

NOW = datetime(2026, 8, 21, tzinfo=UTC)


class FakeSource:
    def __init__(self) -> None:
        self.committed: list[int] = []
        self.fail_next_commit = False

    def poll(self, timeout_s: float) -> RawMessage | None:
        return None

    def commit(self, message: RawMessage) -> None:
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise SystemExit("killed before offset commit")
        self.committed.append(message.offset)

    def close(self) -> None:
        pass


class FakeDeadLetters:
    def __init__(self) -> None:
        self.parked: list[tuple[int, str, str]] = []

    def park(self, message: RawMessage, *, reason: str, error: str, group: str) -> None:
        self.parked.append((message.offset, reason, error))


class AuditingHandler:
    group = "test-auditor"
    topics = ("onramp.cases",)

    def __init__(self, failures: int = 0) -> None:
        self.handles = {"case.escalated": frozenset({1})}
        self.failures_left = failures

    def handle(self, session: Session, envelope: EventEnvelope) -> None:
        if self.failures_left:
            self.failures_left -= 1
            raise ConnectionError("downstream unavailable")
        record_audit(
            session,
            tenant_id=envelope.tenant_id,
            actor=Actor.system(),
            subject_type="case",
            subject_id=envelope.aggregate_id,
            action="test.observed",
            occurred_at=NOW,
            causation_id=envelope.event_id,
        )


def _message(offset: int, event_id: str = "evt_1", **overrides: Any) -> RawMessage:
    envelope: dict[str, Any] = {
        "event_id": event_id,
        "event_type": "case.escalated",
        "schema_version": 1,
        "occurred_at": NOW.isoformat(),
        "tenant_id": "acme",
        "aggregate_type": "case",
        "aggregate_id": "case_1",
        "aggregate_version": 1,
        "correlation_id": "corr_1",
        "payload": serialize_payload(CaseEscalatedV1(reason="r", from_state="created")),
    }
    envelope.update(overrides)
    return RawMessage(
        "onramp.cases", 0, offset, b"case_1", EventEnvelope.model_validate(envelope).to_json()
    )


@pytest.fixture
def sessions(onramp_sessions: sessionmaker[Session]) -> sessionmaker[Session]:
    with onramp_sessions() as session, session.begin():
        session.add(Tenant(id="acme", name="Acme", payout_approval_limit_cents=1))
    return onramp_sessions


def _runner(
    sessions: sessionmaker[Session], handler: AuditingHandler | None = None
) -> tuple[ConsumerRunner, FakeSource, FakeDeadLetters]:
    source, dlq = FakeSource(), FakeDeadLetters()
    runner = ConsumerRunner(
        sessions, source, dlq, handler or AuditingHandler(), sleep=lambda _: None
    )
    return runner, source, dlq


def _effects(sessions: sessionmaker[Session]) -> int:
    with sessions() as session:
        return (
            session.scalar(
                select(func.count()).select_from(AuditEvent).filter_by(action="test.observed")
            )
            or 0
        )


def test_duplicate_delivery_applies_once(sessions: sessionmaker[Session]) -> None:
    runner, source, _ = _runner(sessions)

    outcomes = [runner.process(_message(0)), runner.process(_message(1))]

    assert outcomes == [Outcome.APPLIED, Outcome.DUPLICATE]
    assert _effects(sessions) == 1
    assert source.committed == [0, 1]


def test_crash_after_db_commit_before_offset_commit(sessions: sessionmaker[Session]) -> None:
    runner, source, _ = _runner(sessions)
    source.fail_next_commit = True

    with pytest.raises(SystemExit):
        runner.process(_message(0))
    assert source.committed == []  # offset not committed: Kafka will redeliver

    assert runner.process(_message(0)) is Outcome.DUPLICATE
    assert _effects(sessions) == 1
    assert source.committed == [0]


def test_unhandled_event_types_are_ignored_and_committed(sessions: sessionmaker[Session]) -> None:
    runner, source, dlq = _runner(sessions)

    outcome = runner.process(
        _message(0, event_type="case.resolved", payload={"resolution_code": "PAID_IN_FULL"})
    )

    assert outcome is Outcome.IGNORED
    assert source.committed == [0]
    assert dlq.parked == []


def test_unsupported_version_is_parked_once(sessions: sessionmaker[Session]) -> None:
    runner, _, dlq = _runner(sessions)

    first = runner.process(_message(0, schema_version=2))
    replay = runner.process(_message(1, schema_version=2))

    assert (first, replay) == (Outcome.PARKED, Outcome.DUPLICATE)
    assert dlq.parked == [(0, "unsupported_version:2", "")]
    assert _effects(sessions) == 0


def test_malformed_message_is_parked(sessions: sessionmaker[Session]) -> None:
    runner, source, dlq = _runner(sessions)

    outcome = runner.process(RawMessage("onramp.cases", 0, 7, None, b'{"not": "an envelope"}'))

    assert outcome is Outcome.PARKED
    assert dlq.parked[0][:2] == (7, "malformed_envelope")
    assert source.committed == [7]


def test_transient_handler_failure_is_retried(sessions: sessionmaker[Session]) -> None:
    runner, _, dlq = _runner(sessions, AuditingHandler(failures=2))

    assert runner.process(_message(0)) is Outcome.APPLIED
    assert _effects(sessions) == 1
    assert dlq.parked == []


def test_persistent_handler_failure_is_parked_without_effect(
    sessions: sessionmaker[Session],
) -> None:
    runner, source, dlq = _runner(sessions, AuditingHandler(failures=99))

    assert runner.process(_message(0)) is Outcome.PARKED
    assert dlq.parked == [(0, "handler_failed", "ConnectionError: downstream unavailable")]
    assert _effects(sessions) == 0
    assert source.committed == [0]
    with sessions() as session:
        row = session.scalars(select(ProcessedEvent)).one()
        assert row.outcome == "parked"


class DatabaseDownHandler(AuditingHandler):
    def handle(self, session: Session, envelope: EventEnvelope) -> None:
        raise OperationalError("SELECT 1", {}, ConnectionRefusedError("db down"))


def test_database_outage_stops_the_consumer_instead_of_parking(
    sessions: sessionmaker[Session],
) -> None:
    source, dlq = FakeSource(), FakeDeadLetters()
    runner = ConsumerRunner(sessions, source, dlq, DatabaseDownHandler(), sleep=lambda _: None)

    with pytest.raises(OperationalError):
        runner.process(_message(0))

    assert dlq.parked == []
    assert source.committed == []  # redelivered after restart
