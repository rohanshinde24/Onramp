from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from onramp.db.base import OnrampBase
from onramp.db.types import ID_LENGTH


class OutboxEvent(OnrampBase):
    """An event committed with the change it describes, waiting to be published (ADR-005)."""

    __tablename__ = "outbox_event"
    __table_args__ = (
        UniqueConstraint("event_id"),
        UniqueConstraint("aggregate_type", "aggregate_id", "aggregate_version"),
        # The publisher's claim query reads only unpublished rows that are due.
        Index(
            "ix_outbox_event_unpublished",
            "next_attempt_at",
            "id",
            postgresql_where=text("published_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)
    topic: Mapped[str] = mapped_column(String(128), nullable=False)
    partition_key: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    envelope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(Text)


class AggregateSequence(OnrampBase):
    """Last event version issued per aggregate. Incremented in the emitting transaction, so
    versions are gap-free per committed transaction order and never reused."""

    __tablename__ = "aggregate_sequence"
    __table_args__ = (PrimaryKeyConstraint("aggregate_type", "aggregate_id"),)

    aggregate_type: Mapped[str] = mapped_column(String(32))
    aggregate_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    last_version: Mapped[int] = mapped_column(Integer, nullable=False)


class ProcessedEvent(OnrampBase):
    """One row per (consumer group, event) a consumer has finished with. Inserted in the same
    transaction as the consumer's effect, so a redelivered event is recognised and skipped."""

    __tablename__ = "processed_event"
    __table_args__ = (PrimaryKeyConstraint("consumer_group", "event_id"),)

    consumer_group: Mapped[str] = mapped_column(String(128))
    event_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
