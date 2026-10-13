"""Events the simulated marketplace publishes about its own records."""

from datetime import datetime

from sqlalchemy.orm import Session

from onramp.events.contracts import Payload
from onramp.events.outbox import OutboxStore, emit
from onramp.marketplace.models import MarketplaceAggregateSequence, MarketplaceOutboxEvent

MARKETPLACE_OUTBOX = OutboxStore(MarketplaceOutboxEvent, MarketplaceAggregateSequence)


def publish(
    session: Session,
    event_type: str,
    *,
    tenant_id: str,
    aggregate_id: str,
    payload: Payload,
    occurred_at: datetime,
) -> None:
    emit(
        session,
        event_type=event_type,
        tenant_id=tenant_id,
        aggregate_id=aggregate_id,
        payload=payload,
        occurred_at=occurred_at,
        store=MARKETPLACE_OUTBOX,
    )
