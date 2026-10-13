import os
import time
import uuid
from collections.abc import Callable, Iterator

import pytest
from confluent_kafka import Consumer, Message

from onramp.events.kafka import ensure_topics

BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092")


@pytest.fixture(scope="session")
def bootstrap() -> str:
    return BOOTSTRAP


@pytest.fixture
def fresh_topic(bootstrap: str) -> str:
    name = f"test.{uuid.uuid4().hex[:12]}"
    ensure_topics(bootstrap, [name], partitions=3, replication_factor=1)
    return name


ReadTopic = Callable[[str, int], list[Message]]


@pytest.fixture
def read_topic(bootstrap: str) -> Iterator[ReadTopic]:
    consumers: list[Consumer] = []

    def read(topic: str, expected: int, timeout_s: float = 15.0) -> list[Message]:
        consumer = Consumer(
            {
                "bootstrap.servers": bootstrap,
                "group.id": f"test-reader-{uuid.uuid4().hex[:8]}",
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
            }
        )
        consumers.append(consumer)
        consumer.subscribe([topic])
        messages: list[Message] = []

        deadline = time.monotonic() + timeout_s
        while len(messages) < expected and time.monotonic() < deadline:
            msg = consumer.poll(0.5)
            if msg is not None and msg.error() is None:
                messages.append(msg)
        return messages

    yield read
    for consumer in consumers:
        consumer.close()
