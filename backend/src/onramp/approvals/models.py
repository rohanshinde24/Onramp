from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from onramp.db.base import OnrampBase
from onramp.db.types import id_column


class Decision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class Approval(OnrampBase):
    """A person's decision on an action. One per proposal, enforced by the database, so a
    replayed or duplicated approval cannot be recorded twice."""

    __tablename__ = "approval"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_approval_tenant_id_id"),
        UniqueConstraint("tenant_id", "proposal_id"),
        ForeignKeyConstraint(["tenant_id", "proposal_id"], ["proposal.tenant_id", "proposal.id"]),
        ForeignKeyConstraint(["tenant_id", "approver_id"], ["app_user.tenant_id", "app_user.id"]),
        CheckConstraint("decision IN ('approved', 'rejected')", name="known_decision"),
    )

    id: Mapped[str] = id_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    proposal_id: Mapped[str] = id_column()
    approver_id: Mapped[str] = id_column()
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    # The proposal content the approver saw. Must equal the proposal's hash.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_class: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
