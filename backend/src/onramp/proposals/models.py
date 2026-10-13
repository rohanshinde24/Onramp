from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from onramp.cases.resolutions import ActionType, ResolutionCode, RiskClass
from onramp.db.base import OnrampBase
from onramp.db.types import ID_LENGTH, created_at_column, id_column


class ProposalStatus(StrEnum):
    PENDING = "pending"  # waiting for a human to accept or reject (agent proposals)
    ACCEPTED = "accepted"  # a human stands behind it; resolved, or sent for approval
    REJECTED = "rejected"


def _in(column: str, values: list[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Proposal(OnrampBase):
    """A proposed resolution for a case, with the action it requires if any.

    Never executed directly. ``content_hash`` covers everything an approver approves, so an
    approval binds to exactly this content.
    """

    __tablename__ = "proposal"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_proposal_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "case_id"], ["support_case.tenant_id", "support_case.id"]
        ),
        CheckConstraint(_in("proposer_type", ["user", "agent"]), name="known_proposer_type"),
        CheckConstraint("(proposer_type = 'user') = (proposer_id IS NOT NULL)", name="user_has_id"),
        CheckConstraint(
            _in("resolution_code", [c.value for c in ResolutionCode]), name="known_resolution"
        ),
        CheckConstraint(
            f"action_type IS NULL OR {_in('action_type', [a.value for a in ActionType])}",
            name="known_action",
        ),
        CheckConstraint(
            f"risk_class IS NULL OR {_in('risk_class', [r.value for r in RiskClass])}",
            name="known_risk",
        ),
        CheckConstraint("(action_type IS NULL) = (risk_class IS NULL)", name="risk_iff_action"),
        CheckConstraint(_in("status", [s.value for s in ProposalStatus]), name="known_status"),
        Index(
            "uq_proposal_one_pending_per_case",
            "tenant_id",
            "case_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[str] = id_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    case_id: Mapped[str] = id_column()
    proposer_type: Mapped[str] = mapped_column(String(16), nullable=False)
    proposer_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))

    resolution_code: Mapped[str] = mapped_column(String(64), nullable=False)
    action_type: Mapped[str | None] = mapped_column(String(64))
    action_params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    risk_class: Mapped[str | None] = mapped_column(String(16))
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    decision_note: Mapped[str | None] = mapped_column(Text)
    case_version: Mapped[int] = mapped_column(Integer, nullable=False)
