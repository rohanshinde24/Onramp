import uuid

from onramp.events.kafka import KafkaSink, ensure_topics, producer_config
from onramp.events.publisher import OutboundMessage
from tests.kafka.conftest import ReadTopic


def _sink(bootstrap: str, timeout_ms: int = 10_000) -> KafkaSink:
    return KafkaSink(producer_config(bootstrap, "onramp-test", timeout_ms))


def test_ensure_topics_is_idempotent(bootstrap: str) -> None:
    name = f"test.{uuid.uuid4().hex[:12]}"

    assert ensure_topics(bootstrap, [name], partitions=3, replication_factor=1) == [name]
    assert ensure_topics(bootstrap, [name], partitions=3, replication_factor=1) == []


def test_messages_arrive_with_key_value_and_headers(
    bootstrap: str, fresh_topic: str, read_topic: ReadTopic
) -> None:
    sink = _sink(bootstrap)
    message = OutboundMessage(
        event_id="evt_1",
        topic=fresh_topic,
        key="case_1",
        value=b'{"x": 1}',
        headers={"event_type": "case.escalated", "schema_version": "1"},
    )

    results = sink.publish([message])

    assert [r.ok for r in results] == [True]
    (received,) = read_topic(fresh_topic, 1)
    assert received.key() == b"case_1"
    assert received.value() == b'{"x": 1}'
    assert dict(received.headers() or []) == {
        "event_type": b"case.escalated",
        "schema_version": b"1",
    }


def test_same_key_goes_to_one_partition(
    bootstrap: str, fresh_topic: str, read_topic: ReadTopic
) -> None:
    sink = _sink(bootstrap)
    messages = [
        OutboundMessage(f"evt_{i}", fresh_topic, "case_hot", str(i).encode()) for i in range(10)
    ]

    sink.publish(messages)

    received = read_topic(fresh_topic, 10)
    assert len({m.partition() for m in received}) == 1
    assert [m.value() for m in received] == [str(i).encode() for i in range(10)]


def test_missing_topic_fails_within_the_delivery_timeout(bootstrap: str) -> None:
    sink = _sink(bootstrap, timeout_ms=2_000)
    missing = OutboundMessage("evt_x", f"test.missing.{uuid.uuid4().hex[:8]}", "k", b"v")

    (result,) = sink.publish([missing])

    assert not result.ok
