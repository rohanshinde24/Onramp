"""Every Kafka topic Onramp uses, in one place (ADR-004, EVENTS.md).

Topics are created by ``onramp-kafka-topics``; brokers do not auto-create them.
"""

from dataclasses import dataclass

DLQ_SUFFIX = ".dlq"


@dataclass(frozen=True)
class Topic:
    name: str
    purpose: str


DOMAIN_TOPICS = (
    Topic("onramp.cases", "case lifecycle facts, keyed by case_id"),
    Topic("onramp.actions", "approvals and action results, keyed by case_id"),
    Topic("marketplace.payments", "payment authorization and settlement, keyed by payment_id"),
    Topic("marketplace.work", "submissions and quality reviews, keyed by submission_id"),
    Topic("marketplace.workers", "worker profile, verification and uploads, keyed by worker_id"),
)


def dlq(topic: str) -> str:
    return f"{topic}{DLQ_SUFFIX}"


def all_topic_names() -> list[str]:
    names = [t.name for t in DOMAIN_TOPICS]
    return names + [dlq(n) for n in names]
