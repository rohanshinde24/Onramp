"""Outbox publisher (ADR-005).

Each cycle, in one database transaction:

1. claim up to ``batch_size`` due, unpublished rows with ``FOR UPDATE SKIP LOCKED``,
2. hand them to the sink and wait for per-message acknowledgement,
3. mark acknowledged rows published and schedule retries for the rest, then commit.

Delivery is at least once. If the process dies after the sink acknowledged but before the
commit, the rows stay unpublished and are sent again; consumers deduplicate on ``event_id``.
If it dies before the sink call, nothing was sent and the rows are simply claimed again.

Events are never parked here: a publish failure is a broker problem, not a bad event, so rows
retry with capped backoff and the backlog age is what alerts.
"""

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from opentelemetry import propagate, trace
from opentelemetry.trace import Span, SpanKind, StatusCode
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from onramp.events.envelope import EventEnvelope
from onramp.events.models import OutboxEvent, OutboxEventColumns
from onramp.telemetry.metrics import outbox_publish_failures, outbox_published
from onramp.telemetry.propagation import context_from

log = logging.getLogger(__name__)
_tracer = trace.get_tracer("onramp.events")
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class OutboundMessage:
    event_id: str
    topic: str
    key: str
    value: bytes
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PublishResult:
    event_id: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class EventSink(Protocol):
    def publish(self, messages: Sequence[OutboundMessage]) -> list[PublishResult]:
        """Send messages and block until each is acknowledged or failed."""
        ...


@dataclass(frozen=True)
class CycleStats:
    claimed: int
    published: int
    failed: int


def backoff(attempts: int, base: timedelta = timedelta(seconds=1)) -> timedelta:
    """Exponential, capped at five minutes."""
    factor: int = 2 ** max(attempts - 1, 0)
    return min(base * factor, timedelta(minutes=5))


def _utcnow() -> datetime:
    return datetime.now(UTC)


class OutboxPublisher:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        sink: EventSink,
        *,
        batch_size: int = 100,
        clock: Clock = _utcnow,
        outbox: type[OutboxEventColumns] = OutboxEvent,
    ) -> None:
        self._outbox = outbox
        self._sessions = sessions
        self._sink = sink
        self._batch_size = batch_size
        self._clock = clock

    def run_once(self) -> CycleStats:
        now = self._clock()
        with self._sessions() as session, session.begin():
            outbox = self._outbox
            rows = list(
                session.scalars(
                    select(outbox)
                    .where(outbox.published_at.is_(None), outbox.next_attempt_at <= now)
                    .order_by(outbox.id)
                    .limit(self._batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            if not rows:
                return CycleStats(0, 0, 0)

            # One producer span per event, continuing the trace of the code that emitted it.
            spans = {row.event_id: _publish_span(row) for row in rows}
            try:
                messages = [_message(row, spans[row.event_id]) for row in rows]
                results = {r.event_id: r for r in self._sink.publish(messages)}
                published = failed = 0
                for row in rows:
                    result = results.get(row.event_id, PublishResult(row.event_id, "no ack"))
                    if result.ok:
                        row.published_at = now
                        published += 1
                        outbox_published.add(1, {"topic": row.topic})
                    else:
                        row.attempts += 1
                        row.last_error = (result.error or "")[:1000]
                        row.next_attempt_at = now + backoff(row.attempts)
                        failed += 1
                        outbox_publish_failures.add(1, {"topic": row.topic})
                        spans[row.event_id].set_status(StatusCode.ERROR, row.last_error)
            finally:
                for span in spans.values():
                    span.end()
            if failed:
                log.warning("outbox publish failures: %d of %d", failed, len(rows))
            return CycleStats(len(rows), published, failed)

    def run_until_stopped(
        self,
        stop: threading.Event,
        idle_wait_s: float = 0.5,
        *,
        heartbeat: Callable[[], None] | None = None,
    ) -> None:
        """Publish until ``stop`` is set. A cycle in progress always completes, so SIGTERM never
        leaves a half-marked batch; the next process resumes from committed state.
        ``heartbeat`` runs once per cycle, failed cycles included."""
        while not stop.is_set():
            if heartbeat is not None:
                heartbeat()
            try:
                stats = self.run_once()
            except Exception:
                log.exception("outbox publish cycle failed")
                stop.wait(idle_wait_s)
                continue
            if stats.claimed < self._batch_size:
                stop.wait(idle_wait_s)


def _publish_span(row: OutboxEventColumns) -> Span:
    return _tracer.start_span(
        f"publish {row.topic}",
        context=context_from(row.trace_context),
        kind=SpanKind.PRODUCER,
        attributes={
            "messaging.system": "kafka",
            "messaging.operation.type": "send",
            "messaging.destination.name": row.topic,
            "messaging.message.id": row.event_id,
            "onramp.event_type": row.event_type,
            "onramp.tenant_id": row.tenant_id,
            "onramp.aggregate_id": row.aggregate_id,
            "onramp.outbox.attempts": row.attempts,
        },
    )


def _message(row: OutboxEventColumns, span: Span) -> OutboundMessage:
    envelope = EventEnvelope.model_validate(row.envelope)
    headers = {"event_type": row.event_type, "schema_version": str(row.schema_version)}
    # Consumers continue from this publish span, so the trace shows when the event left.
    propagate.inject(headers, context=trace.set_span_in_context(span))
    return OutboundMessage(
        event_id=row.event_id,
        topic=row.topic,
        key=row.partition_key,
        value=envelope.to_json(),
        headers=headers,
    )


@dataclass(frozen=True)
class Backlog:
    count: int
    oldest_age_s: float


def outbox_backlog(
    session: Session, now: datetime, outbox: type[OutboxEventColumns] = OutboxEvent
) -> Backlog:
    count, oldest = session.execute(
        select(func.count(), func.min(outbox.created_at)).where(outbox.published_at.is_(None))
    ).one()
    age = (now - oldest).total_seconds() if oldest is not None else 0.0
    return Backlog(count=int(count), oldest_age_s=max(age, 0.0))
