"""Replaying parked messages (ADR-005).

A message is parked when a consumer could not handle it: an unsupported version, a malformed
envelope, or a handler that kept failing. After the cause is fixed, replay sends the original
message back to its source topic. The consumer's ``parked`` record is removed first, so its
dedupe check lets the event through again; other consumer groups already processed it and
skip it as a duplicate.

Replay is at least once: the DLQ offset is committed only after the republish is acknowledged.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from onramp.events.consumer import MessageSource, RawMessage
from onramp.events.envelope import EventEnvelope
from onramp.events.models import ProcessedEvent
from onramp.events.publisher import EventSink, OutboundMessage
from onramp.events.topics import DLQ_SUFFIX

log = logging.getLogger(__name__)

_DLQ_HEADERS = ("dlq.reason", "dlq.error", "dlq.consumer_group", "dlq.source")


@dataclass(frozen=True)
class ReplayResult:
    replayed: int
    skipped_other_group: int
    failed: int


def replay_parked(
    dead_letters: MessageSource,
    sink: EventSink,
    sessions: sessionmaker[Session],
    *,
    group: str,
    idle_polls: int = 5,
    should_continue: Callable[[], bool] = lambda: True,
) -> ReplayResult:
    """Replay this group's parked messages until the DLQ has been idle for ``idle_polls``."""
    replayed = skipped = failed = 0
    quiet = 0
    while quiet < idle_polls and should_continue():
        message = dead_letters.poll(1.0)
        if message is None:
            quiet += 1
            continue
        quiet = 0
        if message.headers.get("dlq.consumer_group", b"").decode() != group:
            skipped += 1
            dead_letters.commit(message)
            continue
        if _replay_one(message, sink, sessions, group):
            replayed += 1
            dead_letters.commit(message)
        else:
            failed += 1  # left uncommitted, so the next replay run sees it again
            break
    return ReplayResult(replayed, skipped, failed)


def _replay_one(
    message: RawMessage, sink: EventSink, sessions: sessionmaker[Session], group: str
) -> bool:
    source_topic = message.topic.removesuffix(DLQ_SUFFIX)
    event_id = _event_id(message)
    if event_id is not None:
        with sessions() as session, session.begin():
            session.execute(
                delete(ProcessedEvent).where(
                    ProcessedEvent.consumer_group == group,
                    ProcessedEvent.event_id == event_id,
                    ProcessedEvent.outcome == "parked",
                )
            )
    headers = {
        k: v.decode(errors="replace") for k, v in message.headers.items() if k not in _DLQ_HEADERS
    }
    (result,) = sink.publish(
        [
            OutboundMessage(
                event_id=event_id or f"dlq:{message.partition}:{message.offset}",
                topic=source_topic,
                key=(message.key or b"").decode(errors="replace"),
                value=message.value,
                headers=headers,
            )
        ]
    )
    if not result.ok:
        log.error("replay of %s to %s failed: %s", event_id, source_topic, result.error)
    return result.ok


def _event_id(message: RawMessage) -> str | None:
    try:
        return EventEnvelope.from_json(message.value).event_id
    except ValidationError:
        return None  # malformed: replayed as-is; it will be parked again unless fixed upstream
