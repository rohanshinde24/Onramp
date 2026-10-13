from datetime import datetime
from enum import StrEnum

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
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from onramp.cases.state import TERMINAL_STATES, CaseState
from onramp.db.base import OnrampBase
from onramp.db.types import ID_LENGTH, created_at_column, id_column


class CaseType(StrEnum):
    PAYOUT = "payout"
    ONBOARDING = "onboarding"
    QUALITY_REVIEW = "quality_review"


# The marketplace record a case is about, by case type.
SUBJECT_TYPE: dict[CaseType, str] = {
    CaseType.PAYOUT: "submission",
    CaseType.ONBOARDING: "worker",
    CaseType.QUALITY_REVIEW: "quality_review",
}


def _in(column: str, values: list[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


_TERMINAL = ", ".join(repr(s.value) for s in TERMINAL_STATES)
_SUBJECT_MATCHES = " OR ".join(
    f"(case_type = '{t.value}' AND subject_type = '{s}')" for t, s in SUBJECT_TYPE.items()
)


class SupportCase(OnrampBase):
    __tablename__ = "support_case"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_support_case_tenant_id_id"),
        UniqueConstraint("tenant_id", "intake_key"),
        ForeignKeyConstraint(["tenant_id", "assignee_id"], ["app_user.tenant_id", "app_user.id"]),
        CheckConstraint(_in("case_type", [t.value for t in CaseType]), name="known_case_type"),
        CheckConstraint(_in("state", [s.value for s in CaseState]), name="known_state"),
        CheckConstraint(_SUBJECT_MATCHES, name="subject_matches_case_type"),
        CheckConstraint("version > 0", name="positive_version"),
        # At most one open case per subject and type; a repeat report attaches to it.
        Index(
            "uq_support_case_one_open_per_subject",
            "tenant_id",
            "case_type",
            "subject_id",
            unique=True,
            postgresql_where=text(f"state NOT IN ({_TERMINAL})"),
        ),
        Index("ix_support_case_inbox", "tenant_id", "state", "created_at"),
    )

    id: Mapped[str] = id_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    case_type: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    # Optimistic concurrency: every update bumps this, and a stale update fails.
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = id_column()
    # Resolved server-side from the subject record, never taken from the request.
    worker_id: Mapped[str] = id_column()

    incident_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    incident_basis: Mapped[str] = mapped_column(String(64), nullable=False)

    intake_key: Mapped[str] = mapped_column(String(200), nullable=False)
    # Free text from the reporter. Untrusted: evidence at most, never an instruction.
    reported_description: Mapped[str] = mapped_column(Text, nullable=False)
    opened_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    opened_by_id: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)
    assignee_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))

    resolution_code: Mapped[str | None] = mapped_column(String(64))
    escalation_reason: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
