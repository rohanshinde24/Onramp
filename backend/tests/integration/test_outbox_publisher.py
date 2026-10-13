"""Outbox publisher semantics, including both crash windows from ADR-005."""

import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.events.contracts import CaseEscalatedV1
from onramp.events.envelope import EventEnvelope
from onramp.events.models import OutboxEvent
from onramp.events.outbox import emit
from onramp.events.publisher import (
    OutboundMessage,
    OutboxPublisher,
    PublishResult,
    backoff,
    outbox_backlog,
)

# Rows become due at the database's now(), so the publisher clock runs just after real time.
T0 = datetime.now(UTC) + timedelta(minutes=1)


class RecordingSink:
    """In-memory broker. ``fail`` holds event IDs to reject; ``crash_after_ack`` simulates the
    process dying after the broker acknowledged but before the outbox commit."""

    def __init__(self) -> None:
        self.delivered: list[OutboundMessage] = []
        self.fail: set[str] = set()
        self.crash_before_send = False
        self.crash_after_ack = False
        self.lock = threading.Lock()

    def publish(self, messages: Sequence[OutboundMessage]) -> list[PublishResult]:
        if self.crash_before_send:
            raise SystemExit("killed before send")
        results = []
        with self.lock:
            for m in messages:
                if m.event_id in self.fail:
                    results.append(PublishResult(m.event_id, "broker unavailable"))
                else:
                    self.delivered.append(m)
                    results.append(PublishResult(m.event_id))
        if self.crash_after_ack:
            raise SystemExit("killed after ack")
        return results

    def event_ids(self) -> list[str]:
        return [m.event_id for m in self.delivered]


def _emit_events(sessions: sessionmaker[Session], count: int, case_id: str = "case_1") -> list[str]:
    ids = []
    with sessions() as session, session.begin():
        for _ in range(count):
            ids.append(
                emit(
                    session,
                    event_type="case.escalated",
                    tenant_id="acme",
                    aggregate_id=case_id,
                    payload=CaseEscalatedV1(reason="operator_judgment", from_state="created"),
                    occurred_at=T0,
                ).event_id
            )
    return ids


def _unpublished(sessions: sessionmaker[Session]) -> list[str]:
    with sessions() as session:
        return list(
            session.scalars(select(OutboxEvent.event_id).where(OutboxEvent.published_at.is_(None)))
        )


def test_publishes_with_topic_key_headers_and_full_envelope(
    onramp_sessions: sessionmaker[Session],
) -> None:
    (event_id,) = _emit_events(onramp_sessions, 1)
    sink = RecordingSink()

    stats = OutboxPublisher(onramp_sessions, sink, clock=lambda: T0).run_once()

    assert (stats.claimed, stats.published, stats.failed) == (1, 1, 0)
    (message,) = sink.delivered
    assert (message.topic, message.key) == ("onramp.cases", "case_1")
    assert message.headers == {"event_type": "case.escalated", "schema_version": "1"}
    assert EventEnvelope.from_json(message.value).event_id == event_id
    assert _unpublished(onramp_sessions) == []


def test_crash_before_send_publishes_nothing_and_loses_nothing(
    onramp_sessions: sessionmaker[Session],
) -> None:
    ids = _emit_events(onramp_sessions, 3)
    sink = RecordingSink()
    sink.crash_before_send = True

    with pytest.raises(SystemExit):
        OutboxPublisher(onramp_sessions, sink, clock=lambda: T0).run_once()
    assert sink.delivered == []
    assert sorted(_unpublished(onramp_sessions)) == sorted(ids)

    sink.crash_before_send = False
    OutboxPublisher(onramp_sessions, sink, clock=lambda: T0).run_once()
    assert sink.event_ids() == ids


def test_crash_after_ack_republishes_the_same_events(
    onramp_sessions: sessionmaker[Session],
) -> None:
    ids = _emit_events(onramp_sessions, 2)
    sink = RecordingSink()
    sink.crash_after_ack = True

    with pytest.raises(SystemExit):
        OutboxPublisher(onramp_sessions, sink, clock=lambda: T0).run_once()
    assert sorted(_unpublished(onramp_sessions)) == sorted(ids)  # mark never committed

    sink.crash_after_ack = False
    OutboxPublisher(onramp_sessions, sink, clock=lambda: T0).run_once()

    # At least once: each event delivered twice, with the same event_id both times.
    assert sink.event_ids() == ids + ids
    assert _unpublished(onramp_sessions) == []


def test_failed_messages_retry_with_backoff(onramp_sessions: sessionmaker[Session]) -> None:
    good, bad = _emit_events(onramp_sessions, 2)
    sink = RecordingSink()
    sink.fail = {bad}

    stats = OutboxPublisher(onramp_sessions, sink, clock=lambda: T0).run_once()
    assert (stats.published, stats.failed) == (1, 1)

    sink.fail = set()
    too_soon = OutboxPublisher(
        onramp_sessions, sink, clock=lambda: T0 + timedelta(milliseconds=500)
    )
    assert too_soon.run_once().claimed == 0

    later = OutboxPublisher(onramp_sessions, sink, clock=lambda: T0 + backoff(1))
    assert later.run_once().published == 1
    assert sink.event_ids() == [good, bad]
    with onramp_sessions() as session:
        row = session.scalars(select(OutboxEvent).filter_by(event_id=bad)).one()
        assert (row.attempts, row.last_error) == (1, "broker unavailable")


def test_backoff_is_exponential_and_capped() -> None:
    assert [backoff(n).total_seconds() for n in (1, 2, 3, 4)] == [1, 2, 4, 8]
    assert backoff(30) == timedelta(minutes=5)


def test_backlog_reports_count_and_oldest_age(onramp_sessions: sessionmaker[Session]) -> None:
    _emit_events(onramp_sessions, 3)
    with onramp_sessions() as session:
        oldest = session.scalars(select(OutboxEvent.created_at).order_by(OutboxEvent.id)).first()
        assert oldest is not None
        backlog = outbox_backlog(session, oldest + timedelta(seconds=42))

    assert backlog.count == 3
    assert backlog.oldest_age_s == pytest.approx(42, abs=1)


def test_concurrent_publishers_never_claim_the_same_row(
    onramp_committed: sessionmaker[Session],
) -> None:
    for i in range(10):
        _emit_events(onramp_committed, 20, case_id=f"case_{i}")
    sink = RecordingSink()
    publisher = OutboxPublisher(onramp_committed, sink, batch_size=7)

    def drain(_: int) -> None:
        while publisher.run_once().claimed:
            pass

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(drain, range(4)))

    delivered = sink.event_ids()
    assert len(delivered) == 200
    assert len(set(delivered)) == 200  # SKIP LOCKED: no row claimed twice
    assert _unpublished(onramp_committed) == []


def test_run_until_stopped_exits_when_signalled(onramp_sessions: sessionmaker[Session]) -> None:
    _emit_events(onramp_sessions, 1)
    sink = RecordingSink()
    stop = threading.Event()
    publisher = OutboxPublisher(onramp_sessions, sink)

    worker = threading.Thread(target=publisher.run_until_stopped, args=(stop, 0.01))
    worker.start()
    stop.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
