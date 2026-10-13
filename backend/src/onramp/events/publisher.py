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

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from onramp.events.envelope import EventEnvelope
from onramp.events.models import OutboxEvent

log = logging.getLogger(__name__)
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
    ) -> None:
        self._sessions = sessions
        self._sink = sink
        self._batch_size = batch_size
        self._clock = clock

    def run_once(self) -> CycleStats:
        now = self._clock()
        with self._sessions() as session, session.begin():
            rows = list(
                session.scalars(
                    select(OutboxEvent)
                    .where(OutboxEvent.published_at.is_(None), OutboxEvent.next_attempt_at <= now)
                    .order_by(OutboxEvent.id)
                    .limit(self._batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            if not rows:
                return CycleStats(0, 0, 0)

            results = {r.event_id: r for r in self._sink.publish([_message(row) for row in rows])}
            published = failed = 0
            for row in rows:
                result = results.get(row.event_id, PublishResult(row.event_id, "no ack"))
                if result.ok:
                    row.published_at = now
                    published += 1
                else:
                    row.attempts += 1
                    row.last_error = (result.error or "")[:1000]
                    row.next_attempt_at = now + backoff(row.attempts)
                    failed += 1
            if failed:
                log.warning("outbox publish failures: %d of %d", failed, len(rows))
            return CycleStats(len(rows), published, failed)

    def run_until_stopped(self, stop: threading.Event, idle_wait_s: float = 0.5) -> None:
        """Publish until ``stop`` is set. A cycle in progress always completes, so SIGTERM never
        leaves a half-marked batch; the next process resumes from committed state."""
        while not stop.is_set():
            try:
                stats = self.run_once()
            except Exception:
                log.exception("outbox publish cycle failed")
                stop.wait(idle_wait_s)
                continue
            if stats.claimed < self._batch_size:
                stop.wait(idle_wait_s)


def _message(row: OutboxEvent) -> OutboundMessage:
    envelope = EventEnvelope.model_validate(row.envelope)
    return OutboundMessage(
        event_id=row.event_id,
        topic=row.topic,
        key=row.partition_key,
        value=envelope.to_json(),
        headers={"event_type": row.event_type, "schema_version": str(row.schema_version)},
    )


@dataclass(frozen=True)
class Backlog:
    count: int
    oldest_age_s: float


def outbox_backlog(session: Session, now: datetime) -> Backlog:
    count, oldest = session.execute(
        select(func.count(), func.min(OutboxEvent.created_at)).where(
            OutboxEvent.published_at.is_(None)
        )
    ).one()
    age = (now - oldest).total_seconds() if oldest is not None else 0.0
    return Backlog(count=int(count), oldest_age_s=max(age, 0.0))
