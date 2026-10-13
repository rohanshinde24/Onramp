from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from onramp.db.base import OnrampBase
from onramp.db.types import ID_LENGTH, id_column


class ActorType(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"
    MARKETPLACE_SERVICE = "marketplace_service"


_ACTOR_TYPES = ", ".join(f"'{a.value}'" for a in ActorType)


class AuditEvent(OnrampBase):
    """Immutable record of who or what did what, to which subject, and why.

    A database trigger rejects UPDATE and DELETE (migration 0002).
    """

    __tablename__ = "audit_event"
    __table_args__ = (
        CheckConstraint(f"actor_type IN ({_ACTOR_TYPES})", name="known_actor_type"),
        CheckConstraint(
            "(actor_type IN ('user', 'marketplace_service')) = (actor_id IS NOT NULL)",
            name="actor_id_matches_type",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(ID_LENGTH), unique=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = id_column()
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    causation_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
