from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.events.consumer import ConsumerRunner, Outcome, RawMessage
from onramp.events.models import ProcessedEvent
from onramp.events.publisher import OutboundMessage, PublishResult
from onramp.events.replay import replay_parked
from onramp.identity.models import Tenant
from tests.integration.test_consumer_runner import AuditingHandler, FakeSource, _effects, _message

NOW = datetime(2026, 8, 21, tzinfo=UTC)


class CapturingDeadLetters:
    """Records parked messages the way KafkaDeadLetterSink writes them."""

    def __init__(self) -> None:
        self.messages: list[RawMessage] = []

    def park(self, message: RawMessage, *, reason: str, error: str, group: str) -> None:
        self.messages.append(
            RawMessage(
                topic=f"{message.topic}.dlq",
                partition=0,
                offset=len(self.messages),
                key=message.key,
                value=message.value,
                headers={
                    **message.headers,
                    "trace": b"keep-me",
                    "dlq.reason": reason.encode(),
                    "dlq.error": error.encode(),
                    "dlq.consumer_group": group.encode(),
                    "dlq.source": b"onramp.cases/0/0",
                },
            )
        )


class ListSource:
    def __init__(self, messages: list[RawMessage]) -> None:
        self.pending = list(messages)
        self.committed: list[int] = []

    def poll(self, timeout_s: float) -> RawMessage | None:
        return self.pending.pop(0) if self.pending else None

    def commit(self, message: RawMessage) -> None:
        self.committed.append(message.offset)

    def close(self) -> None:
        pass


class RecordingSink:
    def __init__(self, ok: bool = True) -> None:
        self.sent: list[OutboundMessage] = []
        self.ok = ok

    def publish(self, messages: Sequence[OutboundMessage]) -> list[PublishResult]:
        self.sent.extend(messages)
        return [PublishResult(m.event_id, None if self.ok else "down") for m in messages]


@pytest.fixture
def sessions(onramp_sessions: sessionmaker[Session]) -> sessionmaker[Session]:
    with onramp_sessions() as session, session.begin():
        session.add(Tenant(id="acme", name="Acme", payout_approval_limit_cents=1))
    return onramp_sessions


def _park(sessions: sessionmaker[Session]) -> CapturingDeadLetters:
    dlq = CapturingDeadLetters()
    broken = ConsumerRunner(
        sessions, FakeSource(), dlq, AuditingHandler(failures=99), sleep=lambda _: None
    )
    assert broken.process(_message(0)) is Outcome.PARKED
    return dlq


def test_replayed_event_is_applied_by_the_fixed_consumer(sessions: sessionmaker[Session]) -> None:
    dlq = _park(sessions)
    sink = RecordingSink()

    result = replay_parked(
        ListSource(dlq.messages), sink, sessions, group="test-auditor", idle_polls=1
    )

    assert (result.replayed, result.failed) == (1, 0)
    (sent,) = sink.sent
    assert (sent.topic, sent.key) == ("onramp.cases", "case_1")
    assert sent.headers == {"trace": "keep-me"}  # dlq.* headers stripped, others kept
    with sessions() as session:
        assert session.scalars(select(ProcessedEvent)).all() == []

    fixed = ConsumerRunner(
        sessions, FakeSource(), CapturingDeadLetters(), AuditingHandler(), sleep=lambda _: None
    )
    replayed = RawMessage(sent.topic, 0, 99, sent.key.encode(), sent.value)
    assert fixed.process(replayed) is Outcome.APPLIED
    assert fixed.process(replayed) is Outcome.DUPLICATE
    assert _effects(sessions) == 1


def test_other_groups_messages_are_skipped(sessions: sessionmaker[Session]) -> None:
    dlq = _park(sessions)
    sink = RecordingSink()
    source = ListSource(dlq.messages)

    result = replay_parked(source, sink, sessions, group="someone-else", idle_polls=1)

    assert (result.replayed, result.skipped_other_group) == (0, 1)
    assert sink.sent == []
    with sessions() as session:
        assert session.scalars(select(ProcessedEvent.outcome)).all() == ["parked"]


def test_failed_republish_leaves_the_message_for_the_next_run(
    sessions: sessionmaker[Session],
) -> None:
    dlq = _park(sessions)
    source = ListSource(dlq.messages)

    result = replay_parked(
        source, RecordingSink(ok=False), sessions, group="test-auditor", idle_polls=1
    )

    assert result.failed == 1
    assert source.committed == []
