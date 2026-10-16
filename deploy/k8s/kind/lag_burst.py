"""Builds consumer lag on purpose, to watch KEDA scale the event consumer (ADR-013).

Publishes valid `payment.settled` events for payments that do not exist, in a `loadtest`
tenant that has no cases. The evidence consumer reads each one, finds no payment, records it as
processed and changes nothing else.

usage: backend/.venv/bin/python deploy/k8s/kind/lag_burst.py [count]
Runs from the host against the Compose broker at 127.0.0.1:9092.
"""

import sys
import uuid
from datetime import UTC, datetime

from confluent_kafka import Producer

from onramp.events.envelope import EventEnvelope

TOPIC = "marketplace.payments"


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 20_000
    producer = Producer({"bootstrap.servers": "127.0.0.1:9092", "linger.ms": 20})
    now = datetime.now(UTC)
    for i in range(count):
        payment_id = f"loadtest_pay_{uuid.uuid4().hex[:16]}"
        envelope = EventEnvelope(
            event_id=f"evt_{uuid.uuid4().hex}",
            event_type="payment.settled",
            schema_version=1,
            occurred_at=now,
            tenant_id="loadtest",
            aggregate_type="payment",
            aggregate_id=payment_id,
            aggregate_version=1,
            correlation_id=f"lag-burst-{i}",
            payload={"payment_id": payment_id, "worker_id": "loadtest_w", "amount_cents": 100},
        )
        producer.produce(
            TOPIC,
            key=envelope.partition_key.encode(),
            value=envelope.to_json(),
            headers={"event_type": envelope.event_type, "schema_version": "1"},
        )
        if i % 1000 == 0:
            producer.poll(0)
    remaining = producer.flush(30)
    print(f"published {count - remaining} events to {TOPIC}")


if __name__ == "__main__":
    main()
