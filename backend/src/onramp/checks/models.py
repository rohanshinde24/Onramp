from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from onramp.checks.outcome import OutcomeKind
from onramp.db.base import OnrampBase
from onramp.db.types import ID_LENGTH, created_at_column, id_column

_KINDS = ", ".join(repr(k.value) for k in OutcomeKind) + ", 'error'"


class CheckRun(OnrampBase):
    """One run of a case's deterministic checks: what they concluded and why."""

    __tablename__ = "check_run"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "case_id"], ["support_case.tenant_id", "support_case.id"]
        ),
        CheckConstraint(f"outcome IN ({_KINDS})", name="known_outcome"),
    )

    id: Mapped[str] = id_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    case_id: Mapped[str] = id_column()
    case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_id: Mapped[str | None] = mapped_column(String(64))
    policy_version: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    citations: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    findings: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    missing_items: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    wait_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    proposal_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    created_at: Mapped[datetime] = created_at_column()
