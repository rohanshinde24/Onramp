"""Writing events to the transactional outbox.

``emit`` adds the event to the caller's transaction. If that transaction rolls back, the event
never existed; if it commits, the publisher will eventually deliver it.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from onramp.db.types import new_id
from onramp.events.contracts import Payload, latest_contract, serialize_payload
from onramp.events.envelope import EventEnvelope
from onramp.events.models import AggregateSequence, OutboxEvent
from onramp.telemetry.logging import current_log_context


class ContractMismatchError(TypeError):
    pass


def emit(
    session: Session,
    *,
    event_type: str,
    tenant_id: str,
    aggregate_id: str,
    payload: Payload,
    occurred_at: datetime,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> EventEnvelope:
    contract = latest_contract(event_type)
    if not isinstance(payload, contract.payload):
        raise ContractMismatchError(
            f"{event_type} v{contract.schema_version} expects {contract.payload.__name__}"
        )
    version = _next_version(session, contract.aggregate_type, aggregate_id)
    envelope = EventEnvelope(
        event_id=new_id("evt"),
        event_type=event_type,
        schema_version=contract.schema_version,
        occurred_at=occurred_at,
        tenant_id=tenant_id,
        aggregate_type=contract.aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=version,
        correlation_id=correlation_id or _ambient_correlation_id(),
        causation_id=causation_id,
        payload=serialize_payload(payload),
    )
    session.add(
        OutboxEvent(
            event_id=envelope.event_id,
            topic=contract.topic,
            partition_key=envelope.partition_key,
            event_type=event_type,
            schema_version=contract.schema_version,
            tenant_id=tenant_id,
            aggregate_type=contract.aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=version,
            envelope=envelope.model_dump(mode="json"),
        )
    )
    session.flush()
    return envelope


def _next_version(session: Session, aggregate_type: str, aggregate_id: str) -> int:
    # The upsert takes a row lock, so concurrent emitters for one aggregate serialize here.
    statement = (
        insert(AggregateSequence)
        .values(aggregate_type=aggregate_type, aggregate_id=aggregate_id, last_version=1)
        .on_conflict_do_update(
            index_elements=["aggregate_type", "aggregate_id"],
            set_={"last_version": AggregateSequence.last_version + 1},
        )
        .returning(AggregateSequence.last_version)
    )
    return int(session.execute(statement).scalar_one())


def _ambient_correlation_id() -> str:
    context = current_log_context()
    return context.get("correlation_id") or context.get("request_id") or new_id("corr")


def unpublished(session: Session) -> list[OutboxEvent]:
    return list(
        session.scalars(
            select(OutboxEvent).where(OutboxEvent.published_at.is_(None)).order_by(OutboxEvent.id)
        )
    )
