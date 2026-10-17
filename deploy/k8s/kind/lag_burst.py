"""Builds consumer lag on purpose and times how long the event consumer takes to drain it.

Publishes valid `payment.settled` events for payments that do not exist, in a `loadtest`
tenant that has no cases. The evidence consumer reads each one, finds no payment, records it as
processed and changes nothing else. The script then waits until every event of the burst is in
`processed_event` and reports the drain time from those rows, and the replica counts it saw.

usage: backend/.venv/bin/python deploy/k8s/kind/lag_burst.py [count] [--label TEXT]
Runs from the host against the Compose broker at 127.0.0.1:9092 and the Compose Postgres.
Writes artifacts/drain/<run_id>.json.
"""

import argparse
import json
import shutil
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from confluent_kafka import Producer
from sqlalchemy import func, select

import onramp.models  # noqa: F401
from onramp.config import get_settings
from onramp.db.engine import make_engine, make_session_factory
from onramp.evaluation.report import git_state
from onramp.events.envelope import EventEnvelope
from onramp.events.models import ProcessedEvent

TOPIC = "marketplace.payments"
GROUP = "onramp-case-evidence"
ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("count", type=int, nargs="?", default=20_000)
    parser.add_argument("--label", default="")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"evt_burst{run_id}_"

    producer = Producer({"bootstrap.servers": "127.0.0.1:9092", "linger.ms": 20})
    now = datetime.now(UTC)
    for i in range(args.count):
        payment_id = f"loadtest_pay_{uuid.uuid4().hex[:16]}"
        envelope = EventEnvelope(
            event_id=f"{prefix}{i}",
            event_type="payment.settled",
            schema_version=1,
            occurred_at=now,
            tenant_id="loadtest",
            aggregate_type="payment",
            aggregate_id=payment_id,
            aggregate_version=1,
            correlation_id=f"lag-burst-{run_id}",
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
    producer.flush(60)
    published_at = datetime.now(UTC)
    print(f"published {args.count} events, waiting for {GROUP}", flush=True)

    sessions = make_session_factory(make_engine(str(get_settings().database_url)))
    replicas_seen: set[int] = set()
    deadline = time.monotonic() + args.timeout
    while True:
        with sessions() as session:
            done, distinct, first, last = session.execute(
                select(
                    func.count(),
                    func.count(func.distinct(ProcessedEvent.event_id)),
                    func.min(ProcessedEvent.processed_at),
                    func.max(ProcessedEvent.processed_at),
                ).where(
                    ProcessedEvent.consumer_group == GROUP,
                    ProcessedEvent.event_id.like(f"{prefix}%"),
                )
            ).one()
        replicas_seen.add(_replicas())
        if done >= args.count or time.monotonic() > deadline:
            break
        time.sleep(2)

    report = {
        "run_id": run_id,
        "commit": git_state(ROOT)[0],
        "dirty": git_state(ROOT)[1],
        "label": args.label,
        "events": args.count,
        "processed": done,
        "distinct_processed": distinct,
        "published_at": published_at.isoformat(),
        "first_processed_at": first.isoformat() if first else None,
        "last_processed_at": last.isoformat() if last else None,
        "drain_s": (last - first).total_seconds() if first and last else None,
        "events_per_s": done / (last - first).total_seconds() if first and last else None,
        "consumer_replicas_seen": sorted(replicas_seen),
    }
    out = ROOT / "artifacts" / "drain"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{run_id}.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


def _replicas() -> int:
    kubectl = shutil.which("kubectl") or "kubectl"
    result = subprocess.run(  # noqa: S603 - fixed kubectl call
        [
            kubectl,
            "--context",
            "kind-onramp",
            "-n",
            "onramp",
            "get",
            "deploy",
            "onramp-event-consumer",
            "-o",
            "jsonpath={.status.readyReplicas}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return int(result.stdout or 0)


if __name__ == "__main__":
    main()
