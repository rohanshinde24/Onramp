"""Idempotent event consumption (ADR-005).

For each message:

1. Parse the envelope. A message that is not a valid envelope is parked.
2. Event types the handler does not handle are acknowledged and ignored; topics carry several
   types and a consumer subscribes to the ones it needs. A handled type in a version the
   handler does not support is parked, never guessed at.
3. In one database transaction, insert ``(consumer_group, event_id)`` into ``processed_event``
   and apply the handler's effect. If the row already exists, the event was handled before
   and is skipped.
4. Commit the Kafka offset only after the database commit.

A crash between steps 3 and 4 redelivers the message, and step 3 skips it. A handler that
keeps failing is retried a bounded number of times, then parked to the dead-letter topic and
recorded, so one bad event cannot block its partition forever.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from onramp.events.envelope import EventEnvelope
from onramp.events.models import ProcessedEvent
from onramp.telemetry.logging import log_context

log = logging.getLogger(__name__)

# Failures of the consumer's own dependencies. These never park an event: parking is for
# events that cannot be handled, not for an outage that will pass.
INFRASTRUCTURE_ERRORS: tuple[type[Exception], ...] = (OperationalError, InterfaceError)


@dataclass(frozen=True)
class RawMessage:
    topic: str
    partition: int
    offset: int
    key: bytes | None
    value: bytes
    headers: dict[str, bytes] = field(default_factory=dict)


class MessageSource(Protocol):
    def poll(self, timeout_s: float) -> RawMessage | None: ...
    def commit(self, message: RawMessage) -> None: ...
    def close(self) -> None: ...


class DeadLetterSink(Protocol):
    def park(self, message: RawMessage, *, reason: str, error: str, group: str) -> None: ...


class EventHandler(Protocol):
    @property
    def group(self) -> str: ...

    @property
    def topics(self) -> tuple[str, ...]: ...

    @property
    def handles(self) -> dict[str, frozenset[int]]:
        """event_type -> schema versions this handler supports."""
        ...

    def handle(self, session: Session, envelope: EventEnvelope) -> None:
        """Apply the event's effect in ``session``. Must not commit."""
        ...


class Outcome(StrEnum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"
    PARKED = "parked"


@dataclass
class ConsumerStats:
    counts: dict[Outcome, int] = field(default_factory=lambda: dict.fromkeys(Outcome, 0))

    def add(self, outcome: Outcome) -> None:
        self.counts[outcome] += 1


class ConsumerRunner:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        source: MessageSource,
        dead_letters: DeadLetterSink,
        handler: EventHandler,
        *,
        max_attempts: int = 3,
        retry_delay_s: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sessions = sessions
        self._source = source
        self._dead_letters = dead_letters
        self._handler = handler
        self._max_attempts = max_attempts
        self._retry_delay_s = retry_delay_s
        self._sleep = sleep
        self.stats = ConsumerStats()

    def process(self, message: RawMessage) -> Outcome:
        """Handle one message completely, including its offset commit."""
        outcome = self._outcome(message)
        self._after_commit(message, outcome)
        self._source.commit(message)
        self.stats.add(outcome)
        return outcome

    def _after_commit(self, message: RawMessage, outcome: Outcome) -> None:
        hook = getattr(self._handler, "after_commit", None)
        if hook is None or outcome not in {Outcome.APPLIED, Outcome.DUPLICATE}:
            return
        # Parsing already succeeded in _outcome. Repeat it here so the external effect happens
        # only after the database transaction committed. On failure the Kafka offset remains
        # uncommitted; redelivery repeats this hook even when the database claim is a duplicate.
        envelope = EventEnvelope.from_json(message.value)
        versions = self._handler.handles.get(envelope.event_type)
        if versions is None or envelope.schema_version not in versions:
            return
        hook(envelope, self._sessions)

    def run_until_stopped(
        self,
        stop: threading.Event,
        poll_timeout_s: float = 0.5,
        *,
        on_tick: Callable[[], None] | None = None,
        tick_interval_s: float = 15.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Poll and process until ``stop`` is set. ``on_tick`` runs on this same thread every
        ``tick_interval_s``, so it can query the consumer without cross-thread access."""
        next_tick = monotonic() + tick_interval_s
        try:
            while not stop.is_set():
                message = self._source.poll(poll_timeout_s)
                if message is not None:
                    self.process(message)
                if on_tick is not None and monotonic() >= next_tick:
                    next_tick = monotonic() + tick_interval_s
                    try:
                        on_tick()
                    except Exception:
                        log.exception("consumer tick failed")
        finally:
            self._source.close()

    def _outcome(self, message: RawMessage) -> Outcome:
        group = self._handler.group
        try:
            envelope = EventEnvelope.from_json(message.value)
        except ValidationError as exc:
            self._park(message, "malformed_envelope", f"{exc.error_count()} validation errors")
            return Outcome.PARKED

        with log_context(
            event_id=envelope.event_id,
            tenant_id=envelope.tenant_id,
            correlation_id=envelope.correlation_id,
        ):
            versions = self._handler.handles.get(envelope.event_type)
            if versions is None:
                return Outcome.IGNORED
            if envelope.schema_version not in versions:
                reason = f"unsupported_version:{envelope.schema_version}"
                return self._park_recorded(message, envelope, reason, "")

            last_error = ""
            for attempt in range(1, self._max_attempts + 1):
                try:
                    return self._apply(envelope)
                except INFRASTRUCTURE_ERRORS:
                    # Not the event's fault. Stop without committing the offset; the process
                    # restarts and the message is redelivered once the dependency is back.
                    raise
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"[:1000]
                    log.warning(
                        "%s failed on %s (attempt %d/%d): %s",
                        group,
                        envelope.event_type,
                        attempt,
                        self._max_attempts,
                        last_error,
                    )
                    if attempt < self._max_attempts:
                        self._sleep(self._retry_delay_s * attempt)
            return self._park_recorded(message, envelope, "handler_failed", last_error)

    def _apply(self, envelope: EventEnvelope) -> Outcome:
        with self._sessions() as session, session.begin():
            if not self._claim(session, envelope, Outcome.APPLIED):
                return Outcome.DUPLICATE
            self._handler.handle(session, envelope)
            return Outcome.APPLIED

    def _park_recorded(
        self, message: RawMessage, envelope: EventEnvelope, reason: str, error: str
    ) -> Outcome:
        with self._sessions() as session, session.begin():
            if not self._claim(session, envelope, Outcome.PARKED):
                return Outcome.DUPLICATE
            # Parking happens inside the transaction: if the DLQ write fails, the claim rolls
            # back and the message is retried rather than silently dropped.
            self._park(message, reason, error)
        return Outcome.PARKED

    def _claim(self, session: Session, envelope: EventEnvelope, outcome: Outcome) -> bool:
        claimed = session.execute(
            insert(ProcessedEvent)
            .values(
                consumer_group=self._handler.group,
                event_id=envelope.event_id,
                event_type=envelope.event_type,
                outcome=outcome.value,
            )
            .on_conflict_do_nothing(index_elements=["consumer_group", "event_id"])
            .returning(ProcessedEvent.event_id)
        ).scalar_one_or_none()
        return claimed is not None

    def _park(self, message: RawMessage, reason: str, error: str) -> None:
        log.error("parking message from %s: %s %s", message.topic, reason, error)
        self._dead_letters.park(message, reason=reason, error=error, group=self._handler.group)
