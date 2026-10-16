"""Every Onramp metric, in one place, so the catalog in docs/OPERATIONS.md has one source.

Instruments come from the global meter, which forwards to whatever provider the process role
configured; recording before configuration, or without an exporter, is harmless.

Business counters (transitions, approvals, gateway outcomes) count facts, not attempts: code
that stages them inside a database transaction calls ``record_on_commit`` and they are recorded
only if that transaction commits.
"""

from collections.abc import Mapping
from typing import Any

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Counter, Histogram, Observation
from sqlalchemy import event
from sqlalchemy.orm import Session, SessionTransaction

_meter = metrics.get_meter("onramp")

case_transitions = _meter.create_counter(
    "onramp.case.transitions",
    unit="{transition}",
    description="Committed case state changes, by case type, from and to state, and reason",
)
check_runs = _meter.create_counter(
    "onramp.checks.runs", unit="{run}", description="Deterministic check runs by outcome"
)
check_duration = _meter.create_histogram(
    "onramp.checks.duration", unit="s", description="Time to run a case's checks"
)
investigation_runs = _meter.create_counter(
    "onramp.investigation.runs",
    unit="{run}",
    description="Investigations by status, model and prompt version",
)
investigation_duration = _meter.create_histogram(
    "onramp.investigation.duration", unit="s", description="Wall time of one investigation"
)
investigation_tokens = _meter.create_counter(
    "onramp.investigation.tokens", unit="{token}", description="Model tokens by direction"
)
model_call_duration = _meter.create_histogram(
    "onramp.model.call.duration", unit="s", description="Latency of one model request"
)
tool_calls = _meter.create_counter(
    "onramp.agent.tool_calls", unit="{call}", description="Investigator tool calls by tool"
)
retrieval_duration = _meter.create_histogram(
    "onramp.retrieval.duration", unit="s", description="Policy search latency by retriever"
)
approval_decisions = _meter.create_counter(
    "onramp.approvals.decisions",
    unit="{decision}",
    description="Committed approval decisions by decision and risk class",
)
gateway_executions = _meter.create_counter(
    "onramp.gateway.executions",
    unit="{execution}",
    description="Action Gateway outcomes by action type and result",
)
gateway_duration = _meter.create_histogram(
    "onramp.gateway.duration", unit="s", description="Time to execute and verify one action"
)
marketplace_write_attempts = _meter.create_counter(
    "onramp.marketplace.write.attempts",
    unit="{attempt}",
    description="Marketplace write attempts by action type and result, including retries",
)
outbox_published = _meter.create_counter(
    "onramp.outbox.published", unit="{event}", description="Outbox events sent, by topic"
)
outbox_publish_failures = _meter.create_counter(
    "onramp.outbox.publish_failures", unit="{event}", description="Outbox sends that failed"
)
consumer_messages = _meter.create_counter(
    "onramp.consumer.messages",
    unit="{message}",
    description="Consumed messages by group, event type and outcome",
)
consumer_duration = _meter.create_histogram(
    "onramp.consumer.duration", unit="s", description="Time to fully process one message"
)


class LatestValue:
    """A gauge whose value a process's own periodic loop sets; export reads the latest one.

    Used where reading the value means touching a resource owned by another thread (the Kafka
    consumer) or a database query that should run on the role's schedule, not the exporter's.
    """

    def __init__(self, name: str, unit: str, description: str) -> None:
        self._values: dict[tuple[tuple[str, str], ...], float] = {}
        _meter.create_observable_gauge(
            name, callbacks=[self._observe], unit=unit, description=description
        )

    def set(self, value: float, attributes: Mapping[str, str]) -> None:
        self._values[tuple(sorted(attributes.items()))] = value

    def _observe(self, _: CallbackOptions) -> list[Observation]:
        return [Observation(value, dict(key)) for key, value in list(self._values.items())]


outbox_backlog = LatestValue(
    "onramp.outbox.backlog", "{event}", "Unpublished outbox events, by producing database"
)
outbox_oldest_age = LatestValue(
    "onramp.outbox.oldest_age", "s", "Age of the oldest unpublished outbox event"
)
consumer_lag = LatestValue(
    "onramp.consumer.lag", "{message}", "Messages behind the end of the subscribed partitions"
)

_PENDING = "onramp.pending_metrics"


def record_on_commit(
    session: Session, instrument: Counter | Histogram, value: float, attributes: Mapping[str, Any]
) -> None:
    """Record ``value`` when ``session``'s transaction commits; drop it if the transaction, or
    the savepoint it was staged in, rolls back."""
    staged_in = session.get_nested_transaction() or session.get_transaction()
    session.info.setdefault(_PENDING, []).append((staged_in, instrument, value, dict(attributes)))


@event.listens_for(Session, "after_commit")
def _flush_pending(session: Session) -> None:
    for _, instrument, value, attributes in session.info.pop(_PENDING, []):
        if isinstance(instrument, Counter):
            instrument.add(value, attributes)
        else:
            instrument.record(value, attributes)


@event.listens_for(Session, "after_soft_rollback")
def _drop_rolled_back(session: Session, previous_transaction: SessionTransaction) -> None:
    def inside(transaction: SessionTransaction | None) -> bool:
        while transaction is not None:
            if transaction is previous_transaction:
                return True
            transaction = transaction.parent
        return False

    kept = [entry for entry in session.info.get(_PENDING, []) if not inside(entry[0])]
    if kept:
        session.info[_PENDING] = kept
    else:
        session.info.pop(_PENDING, None)
