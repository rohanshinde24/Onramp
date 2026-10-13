"""proposals

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "proposal",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("proposer_type", sa.String(length=16), nullable=False),
        sa.Column("proposer_id", sa.String(length=64), nullable=True),
        sa.Column("resolution_code", sa.String(length=64), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=True),
        sa.Column("action_params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("risk_class", sa.String(length=16), nullable=True),
        sa.Column("reason_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_id", sa.String(length=64), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("case_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "(proposer_type = 'user') = (proposer_id IS NOT NULL)",
            name=op.f("ck_proposal_user_has_id"),
        ),
        sa.CheckConstraint(
            "action_type IS NULL OR action_type IN ('PAYOUT_ADJUSTMENT', 'ACTIVATE_WORKER', 'OVERTURN_QUALITY_REVIEW')",
            name=op.f("ck_proposal_known_action"),
        ),
        sa.CheckConstraint(
            "proposer_type IN ('user', 'agent')", name=op.f("ck_proposal_known_proposer_type")
        ),
        sa.CheckConstraint(
            "resolution_code IN ('MISSING_PAYOUT', 'UNDERPAID', 'PAID_IN_FULL', 'NOT_ELIGIBLE', 'REQUIREMENTS_MET', 'REQUIREMENT_FAILED', 'ALREADY_ACTIVE', 'REVIEW_UPHELD', 'REVIEW_OVERTURNED', 'OUTSIDE_DISPUTE_WINDOW')",
            name=op.f("ck_proposal_known_resolution"),
        ),
        sa.CheckConstraint(
            "risk_class IS NULL OR risk_class IN ('medium', 'high')",
            name=op.f("ck_proposal_known_risk"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')", name=op.f("ck_proposal_known_status")
        ),
        sa.CheckConstraint(
            "(action_type IS NULL) = (risk_class IS NULL)", name=op.f("ck_proposal_risk_iff_action")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["support_case.tenant_id", "support_case.id"],
            name=op.f("fk_proposal_tenant_id_case_id_support_case"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_proposal_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposal")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_proposal_tenant_id_id"),
    )
    op.create_index(
        "uq_proposal_one_pending_per_case",
        "proposal",
        ["tenant_id", "case_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_proposal_one_pending_per_case",
        table_name="proposal",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_table("proposal")
