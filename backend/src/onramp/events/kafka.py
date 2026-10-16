"""Kafka adapters. Kept thin: semantics live in the publisher and consumer runner."""

import logging
from collections.abc import Callable, Sequence

from confluent_kafka import (
    Consumer,
    KafkaError,
    KafkaException,
    Message,
    Producer,
    TopicPartition,
)
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic

from onramp.events.consumer import RawMessage
from onramp.events.publisher import OutboundMessage, PublishResult
from onramp.events.topics import dlq

log = logging.getLogger(__name__)


def producer_config(bootstrap: str, client_id: str, delivery_timeout_ms: int) -> dict[str, object]:
    return {
        "bootstrap.servers": bootstrap,
        "client.id": client_id,
        # Wait for all in-sync replicas, and let the broker drop duplicates caused by producer
        # retries. This does not cover an outbox republish after a crash, so consumers stay
        # idempotent.
        "acks": "all",
        "enable.idempotence": True,
        "delivery.timeout.ms": delivery_timeout_ms,
        "linger.ms": 5,
    }


class KafkaSink:
    """EventSink backed by a Kafka producer. ``publish`` blocks until every message is acked
    or has failed, bounded by ``delivery.timeout.ms``."""

    def __init__(self, config: dict[str, object]) -> None:
        self._producer = Producer(config)
        self._timeout_s = int(str(config["delivery.timeout.ms"])) / 1000 + 5

    def publish(self, messages: Sequence[OutboundMessage]) -> list[PublishResult]:
        # event_id -> None on success, error text on failure. Missing means no report arrived.
        outcomes: dict[str, str | None] = {}

        def reporter(event_id: str) -> Callable[[KafkaError | None, Message], None]:
            def on_delivery(err: KafkaError | None, _: Message) -> None:
                outcomes[event_id] = None if err is None else str(err)

            return on_delivery

        for m in messages:
            try:
                self._producer.produce(
                    m.topic,
                    key=m.key.encode(),
                    value=m.value,
                    headers=list(m.headers.items()),
                    on_delivery=reporter(m.event_id),
                )
            except (BufferError, KafkaException) as exc:
                outcomes[m.event_id] = f"produce failed: {exc}"
        remaining = self._producer.flush(self._timeout_s)
        if remaining:
            log.warning("%d messages still in flight after flush timeout", remaining)
        return [
            PublishResult(m.event_id, outcomes.get(m.event_id, "no delivery report"))
            for m in messages
        ]

    def close(self) -> None:
        self._producer.flush(self._timeout_s)


def ensure_topics(
    bootstrap: str, names: Sequence[str], *, partitions: int, replication_factor: int
) -> list[str]:
    """Create any missing topics. Returns the names created."""
    admin = AdminClient({"bootstrap.servers": bootstrap})
    existing = set(admin.list_topics(timeout=10).topics)
    missing = [n for n in names if n not in existing]
    if not missing:
        return []
    futures = admin.create_topics(
        [
            NewTopic(n, num_partitions=partitions, replication_factor=replication_factor)
            for n in missing
        ]
    )
    for name, future in futures.items():
        try:
            future.result(timeout=30)
        except KafkaException as exc:
            # Another process may have created it between our check and our request.
            if exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
                raise
            log.info("topic %s already exists", name)
    return missing


def consumer_config(bootstrap: str, group: str, client_id: str) -> dict[str, object]:
    return {
        "bootstrap.servers": bootstrap,
        "group.id": group,
        "client.id": client_id,
        # Offsets are committed by the runner after the database commit, never automatically.
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
    }


class KafkaMessageSource:
    def __init__(self, config: dict[str, object], topics: Sequence[str]) -> None:
        self._consumer = Consumer(config)
        self._topics = list(topics)
        self._consumer.subscribe(self._topics)

    def poll(self, timeout_s: float) -> RawMessage | None:
        msg = self._consumer.poll(timeout_s)
        if msg is None:
            return None
        error = msg.error()
        if error is not None:
            if error.code() != KafkaError._PARTITION_EOF:
                log.warning("consumer error: %s", error)
            return None
        key, value = msg.key(), msg.value()
        headers: dict[str, bytes] = {}
        for header in msg.headers() or []:
            name, raw = header[0], header[1]
            if isinstance(raw, bytes):
                headers[str(name)] = raw
        return RawMessage(
            topic=msg.topic() or "",
            partition=msg.partition() or 0,
            offset=msg.offset() or 0,
            key=key if isinstance(key, bytes) else None,
            value=value if isinstance(value, bytes) else b"",
            headers=headers,
        )

    def commit(self, message: RawMessage) -> None:
        self._consumer.commit(
            offsets=[TopicPartition(message.topic, message.partition, message.offset + 1)],
            asynchronous=False,
        )

    def lag(self) -> int:
        """Messages between this group's committed offsets and the log end, over every
        partition of the subscribed topics.

        Not only the assigned ones: while the group is rebalancing or this member has lost its
        session, the assignment is empty, and that is exactly when lag must not read as zero.
        Raises when the broker cannot be reached, so a caller never reports a stale number.
        """
        metadata = self._consumer.list_topics(timeout=5)
        partitions = [
            TopicPartition(topic, partition)
            for topic in self._topics
            if topic in metadata.topics
            for partition in metadata.topics[topic].partitions
        ]
        if not partitions:
            return 0
        total = 0
        for tp in self._consumer.committed(partitions, timeout=5):
            _, high = self._consumer.get_watermark_offsets(tp, timeout=5, cached=False)
            committed = tp.offset if tp.offset >= 0 else 0
            total += max(high - committed, 0)
        return total

    def close(self) -> None:
        self._consumer.close()


class KafkaDeadLetterSink:
    def __init__(self, config: dict[str, object]) -> None:
        self._producer = Producer(config)

    def park(self, message: RawMessage, *, reason: str, error: str, group: str) -> None:
        headers = {
            **message.headers,
            "dlq.reason": reason.encode(),
            "dlq.error": error.encode(),
            "dlq.consumer_group": group.encode(),
            "dlq.source": f"{message.topic}/{message.partition}/{message.offset}".encode(),
        }
        failures: list[str] = []

        def on_delivery(err: KafkaError | None, _: Message) -> None:
            if err is not None:
                failures.append(str(err))

        self._producer.produce(
            dlq(message.topic),
            key=message.key,
            value=message.value,
            headers=list(headers.items()),
            on_delivery=on_delivery,
        )
        self._producer.flush(30)
        if failures:
            raise KafkaException(failures[0])
