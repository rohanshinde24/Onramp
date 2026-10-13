"""Versioned policies (ADR-003, PRODUCT_SPEC: policy version applicable at incident time).

A policy version is immutable once loaded. Its effective period is ``[effective_from,
effective_until)``; an open-ended version has no ``effective_until``. The database rejects
overlapping periods for one policy, so at most one version applies at any instant.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSTZRANGE, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from onramp.db.base import OnrampBase
from onramp.db.types import created_at_column


class Policy(OnrampBase):
    __tablename__ = "policy"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # The case type whose cases this policy governs.
    domain: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)


class PolicyVersion(OnrampBase):
    __tablename__ = "policy_version"
    __table_args__ = (
        UniqueConstraint("policy_id", "version"),
        CheckConstraint("version > 0", name="positive_version"),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="non_empty_period",
        ),
        ExcludeConstraint(
            ("policy_id", "="),
            ("effective_period", "&&"),
            name="no_overlapping_periods",
            using="gist",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("policy.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Derived range used only by the exclusion constraint.
    effective_period: Mapped[Any] = mapped_column(
        TSTZRANGE, Computed("tstzrange(effective_from, effective_until, '[)')", persisted=True)
    )
    # Structured values deterministic checks use, each tied to the passage that states it:
    # {"settlement_window_days": {"value": 7, "passage_id": "payout_v12_section_4_2"}}
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    loaded_at: Mapped[datetime] = created_at_column()


class PolicyPassage(OnrampBase):
    __tablename__ = "policy_passage"
    __table_args__ = (
        ForeignKeyConstraint(
            ["policy_id", "version"], ["policy_version.policy_id", "policy_version.version"]
        ),
        UniqueConstraint("policy_id", "version", "section"),
    )

    # Stable and human-readable, e.g. payout_v12_section_4_3. Citations use this ID.
    passage_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str] = mapped_column(String(16), nullable=False)
    heading: Mapped[str] = mapped_column(String(200), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
