from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from onramp.audit.models import ActorType, AuditEvent
from onramp.db.types import new_id


@dataclass(frozen=True)
class Actor:
    type: ActorType
    id: str | None = None

    @classmethod
    def user(cls, user_id: str) -> "Actor":
        return cls(ActorType.USER, user_id)

    @classmethod
    def system(cls) -> "Actor":
        return cls(ActorType.SYSTEM)

    @classmethod
    def agent(cls) -> "Actor":
        return cls(ActorType.AGENT)


def record_audit(
    session: Session,
    *,
    tenant_id: str,
    actor: Actor,
    subject_type: str,
    subject_id: str,
    action: str,
    occurred_at: datetime,
    details: dict[str, Any] | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> AuditEvent:
    """Append an audit event in the caller's transaction, so it commits or rolls back with
    the change it describes."""
    event = AuditEvent(
        event_id=new_id("aud"),
        tenant_id=tenant_id,
        occurred_at=occurred_at,
        actor_type=actor.type.value,
        actor_id=actor.id,
        subject_type=subject_type,
        subject_id=subject_id,
        action=action,
        details=details or {},
        correlation_id=correlation_id,
        causation_id=causation_id,
    )
    session.add(event)
    return event
