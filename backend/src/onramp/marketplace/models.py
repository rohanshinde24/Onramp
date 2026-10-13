"""Marketplace database schema.

Every table carries tenant_id, and every reference between marketplace records is a composite
(tenant_id, id) foreign key, so a record cannot point at another tenant's data.
Money is integer cents.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from onramp.db.base import NAMING_CONVENTION
from onramp.db.types import created_at_column, id_column
from onramp.events.models import AggregateSequenceColumns, OutboxEventColumns


class MarketplaceBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _tenant_key(table: str) -> UniqueConstraint:
    return UniqueConstraint("tenant_id", "id", name=f"uq_{table}_tenant_id_id")


def _tenant_fk(column: str, target: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["tenant_id", column], [f"{target}.tenant_id", f"{target}.id"])


WORKER_STATUSES = ("pending_onboarding", "active", "suspended", "denied")
VERIFICATION_KINDS = ("identity", "tax_form", "skills_assessment")
VERIFICATION_STATUSES = ("pending", "passed", "failed", "expired")
ASSIGNMENT_STATUSES = ("assigned", "submitted", "expired")
SUBMISSION_STATUSES = ("pending_review", "accepted", "rejected")
REVIEW_RESULTS = ("pass", "fail")
PAYMENT_KINDS = ("task_payout", "adjustment")
PAYMENT_STATUSES = ("authorized", "settled", "failed")
PAYMENT_EVENT_TYPES = ("authorized", "settlement_succeeded", "settlement_failed")


class MarketplaceTenant(MarketplaceBase):
    __tablename__ = "tenant"

    id: Mapped[str] = id_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class Worker(MarketplaceBase):
    __tablename__ = "worker"
    __table_args__ = (
        _tenant_key("worker"),
        CheckConstraint(_in("status", WORKER_STATUSES), name="known_status"),
    )

    id: Mapped[str] = id_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    # Not unique: namesakes exist, and identity is always the worker ID.
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class VerificationRecord(MarketplaceBase):
    __tablename__ = "verification_record"
    __table_args__ = (
        _tenant_key("verification_record"),
        _tenant_fk("worker_id", "worker"),
        CheckConstraint(_in("kind", VERIFICATION_KINDS), name="known_kind"),
        CheckConstraint(_in("status", VERIFICATION_STATUSES), name="known_status"),
    )

    id: Mapped[str] = id_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    worker_id: Mapped[str] = id_column()
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(MarketplaceBase):
    """A worker-uploaded document. Its text is untrusted input."""

    __tablename__ = "document"
    __table_args__ = (_tenant_key("document"), _tenant_fk("worker_id", "worker"))

    id: Mapped[str] = id_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    worker_id: Mapped[str] = id_column()
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Task(MarketplaceBase):
    __tablename__ = "task"
    __table_args__ = (
        _tenant_key("task"),
        CheckConstraint("payout_cents > 0", name="positive_payout"),
    )

    id: Mapped[str] = id_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payout_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # Review criteria the task instructions ask for. NULL when the instructions do not say,
    # which makes "did this criterion apply?" a judgment rather than a lookup.
    criteria: Mapped[list[str] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_column()


class Assignment(MarketplaceBase):
    __tablename__ = "assignment"
    __table_args__ = (
        _tenant_key("assignment"),
        _tenant_fk("task_id", "task"),
        _tenant_fk("worker_id", "worker"),
        UniqueConstraint("tenant_id", "task_id", "worker_id"),
        CheckConstraint(_in("status", ASSIGNMENT_STATUSES), name="known_status"),
    )

    id: Mapped[str] = id_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    task_id: Mapped[str] = id_column()
    worker_id: Mapped[str] = id_column()
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Submission(MarketplaceBase):
    __tablename__ = "submission"
    __table_args__ = (
        _tenant_key("submission"),
        _tenant_fk("assignment_id", "assignment"),
        UniqueConstraint("tenant_id", "assignment_id"),
        CheckConstraint(_in("status", SUBMISSION_STATUSES), name="known_status"),
    )

    id: Mapped[str] = id_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    assignment_id: Mapped[str] = id_column()
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QualityReview(MarketplaceBase):
    __tablename__ = "quality_review"
    __table_args__ = (
        _tenant_key("quality_review"),
        _tenant_fk("submission_id", "submission"),
        CheckConstraint(_in("result", REVIEW_RESULTS), name="known_result"),
        CheckConstraint("score >= 0 AND score <= 1", name="score_in_unit_range"),
    )

    id: Mapped[str] = id_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    submission_id: Mapped[str] = id_column()
    reviewer_id: Mapped[str] = id_column()
    result: Mapped[str] = mapped_column(String(8), nullable=False)
    score: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    rubric_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reviewer_comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    overturned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Payment(MarketplaceBase):
    __tablename__ = "payment"
    __table_args__ = (
        _tenant_key("payment"),
        _tenant_fk("worker_id", "worker"),
        _tenant_fk("submission_id", "submission"),
        CheckConstraint(_in("kind", PAYMENT_KINDS), name="known_kind"),
        CheckConstraint(_in("status", PAYMENT_STATUSES), name="known_status"),
        CheckConstraint("amount_cents > 0", name="positive_amount"),
        CheckConstraint(
            "(kind = 'task_payout') = (submission_id IS NOT NULL)",
            name="task_payout_has_submission",
        ),
    )

    id: Mapped[str] = id_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    worker_id: Mapped[str] = id_column()
    submission_id: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaymentEvent(MarketplaceBase):
    __tablename__ = "payment_event"
    __table_args__ = (
        _tenant_key("payment_event"),
        _tenant_fk("payment_id", "payment"),
        CheckConstraint(_in("event_type", PAYMENT_EVENT_TYPES), name="known_event_type"),
    )

    id: Mapped[str] = id_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    payment_id: Mapped[str] = id_column()
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String(200))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DocumentRequest(MarketplaceBase):
    """A request sent to a worker to upload a named document."""

    __tablename__ = "document_request"
    __table_args__ = (_tenant_key("document_request"), _tenant_fk("worker_id", "worker"))

    id: Mapped[str] = id_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    worker_id: Mapped[str] = id_column()
    document_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WriteRequest(MarketplaceBase):
    """Idempotency record for every marketplace write.

    A repeated write with the same key returns the stored result instead of acting again.
    """

    __tablename__ = "write_request"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class MarketplaceOutboxEvent(OutboxEventColumns, MarketplaceBase):
    """The marketplace's own outbox. Its relay is separate from Onramp's, as it would be for
    a real external system."""


class MarketplaceAggregateSequence(AggregateSequenceColumns, MarketplaceBase):
    pass
