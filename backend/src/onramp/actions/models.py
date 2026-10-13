from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from onramp.cases.resolutions import ActionType
from onramp.db.base import OnrampBase
from onramp.db.types import created_at_column, id_column


class ExecutionStatus(StrEnum):
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNVERIFIED = "unverified"


def _in(column: str, values: list[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ExecutedAction(OnrampBase):
    """Durable intent and result for one approved marketplace mutation."""

    __tablename__ = "executed_action"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_executed_action_tenant_id_id"),
        UniqueConstraint("tenant_id", "proposal_id"),
        UniqueConstraint("tenant_id", "idempotency_key"),
        ForeignKeyConstraint(
            ["tenant_id", "case_id"], ["support_case.tenant_id", "support_case.id"]
        ),
        ForeignKeyConstraint(["tenant_id", "proposal_id"], ["proposal.tenant_id", "proposal.id"]),
        ForeignKeyConstraint(["tenant_id", "approval_id"], ["approval.tenant_id", "approval.id"]),
        CheckConstraint(
            _in("action_type", [action.value for action in ActionType]), name="known_action"
        ),
        CheckConstraint(
            _in("status", [status.value for status in ExecutionStatus]), name="known_status"
        ),
        CheckConstraint(
            "(status = 'executing' AND completed_at IS NULL) OR "
            "(status <> 'executing' AND completed_at IS NOT NULL)",
            name="completion_matches_status",
        ),
        Index("ix_executed_action_case", "tenant_id", "case_id", "created_at"),
    )

    id: Mapped[str] = id_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    case_id: Mapped[str] = id_column()
    proposal_id: Mapped[str] = id_column()
    approval_id: Mapped[str] = id_column()
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
