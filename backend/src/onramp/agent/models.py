from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from onramp.db.base import OnrampBase
from onramp.db.types import ID_LENGTH, created_at_column, id_column

STATUSES = ("concluded", "budget_exhausted", "invalid_output", "model_error", "superseded")


class InvestigationRun(OnrampBase):
    """One investigation: the model and prompt used, what it did, and what it concluded."""

    __tablename__ = "investigation_run"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "case_id"], ["support_case.tenant_id", "support_case.id"]
        ),
        CheckConstraint(f"status IN ({', '.join(repr(s) for s in STATUSES)})", name="known_status"),
        Index("ix_investigation_run_case", "tenant_id", "case_id", "created_at"),
    )

    id: Mapped[str] = id_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    case_id: Mapped[str] = id_column()
    case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    conclusion: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    problems: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    model_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    elapsed_s: Mapped[float] = mapped_column(Float, nullable=False)
    trajectory: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    proposal_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    created_at: Mapped[datetime] = created_at_column()
