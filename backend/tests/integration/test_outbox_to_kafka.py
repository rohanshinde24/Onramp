"""Outbox rows reach Kafka through the real publisher and producer."""

import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from confluent_kafka import Consumer
from sqlalchemy.orm import Session, sessionmaker

from onramp.events.contracts import CaseEscalatedV1
from onramp.events.kafka import KafkaSink, ensure_topics, producer_config
from onramp.events.outbox import emit
from onramp.events.publisher import OutboxPublisher
from onramp.events.topics import all_topic_names

pytestmark = pytest.mark.kafka

BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


def test_emitted_events_are_published_to_their_topic_keyed_by_case(
    onramp_sessions: sessionmaker[Session],
) -> None:
    ensure_topics(BOOTSTRAP, all_topic_names(), partitions=3, replication_factor=1)
    case_id = f"case_{uuid.uuid4().hex[:8]}"
    with onramp_sessions() as session, session.begin():
        ids = [
            emit(
                session,
                event_type="case.escalated",
                tenant_id="acme",
                aggregate_id=case_id,
                payload=CaseEscalatedV1(reason="operator_judgment", from_state="created"),
                occurred_at=datetime.now(UTC),
            ).event_id
            for _ in range(3)
        ]

    sink = KafkaSink(producer_config(BOOTSTRAP, "onramp-test", 10_000))
    publisher = OutboxPublisher(
        onramp_sessions, sink, clock=lambda: datetime.now(UTC) + timedelta(seconds=1)
    )
    assert publisher.run_once().published == 3

    consumer = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": f"test-{uuid.uuid4().hex[:8]}",
            "auto.offset.reset": "earliest",
        }
    )
    consumer.subscribe(["onramp.cases"])
    seen: list[tuple[bytes | None, str]] = []
    deadline = time.monotonic() + 20
    while len(seen) < 3 and time.monotonic() < deadline:
        msg = consumer.poll(0.5)
        if msg is None or msg.error() is not None:
            continue
        envelope = json.loads(msg.value() or b"{}")
        if envelope.get("aggregate_id") == case_id:
            seen.append((msg.key(), envelope["event_id"]))
    consumer.close()

    assert [event_id for _, event_id in seen] == ids
    assert {key for key, _ in seen} == {case_id.encode()}
