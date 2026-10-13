"""Kafka adapters. Kept thin: semantics live in the publisher and consumer runner."""

import logging
from collections.abc import Callable, Sequence

from confluent_kafka import KafkaError, KafkaException, Message, Producer
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic

from onramp.events.publisher import OutboundMessage, PublishResult

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
