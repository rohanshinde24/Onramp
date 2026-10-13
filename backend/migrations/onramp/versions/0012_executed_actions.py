"""executed actions

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(op.f("uq_approval_tenant_id_id"), "approval", ["tenant_id", "id"])
    op.create_table(
        "executed_action",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("proposal_id", sa.String(length=64), nullable=False),
        sa.Column("approval_id", sa.String(length=64), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "action_type IN ('PAYOUT_ADJUSTMENT', 'ACTIVATE_WORKER', 'OVERTURN_QUALITY_REVIEW')",
            name=op.f("ck_executed_action_known_action"),
        ),
        sa.CheckConstraint(
            "(status = 'executing' AND completed_at IS NULL) OR "
            "(status <> 'executing' AND completed_at IS NOT NULL)",
            name=op.f("ck_executed_action_completion_matches_status"),
        ),
        sa.CheckConstraint(
            "status IN ('executing', 'succeeded', 'failed', 'unverified')",
            name=op.f("ck_executed_action_known_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "approval_id"],
            ["approval.tenant_id", "approval.id"],
            name=op.f("fk_executed_action_tenant_id_approval_id_approval"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["support_case.tenant_id", "support_case.id"],
            name=op.f("fk_executed_action_tenant_id_case_id_support_case"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "proposal_id"],
            ["proposal.tenant_id", "proposal.id"],
            name=op.f("fk_executed_action_tenant_id_proposal_id_proposal"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_executed_action_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_executed_action")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_executed_action_tenant_id_id")),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name=op.f("uq_executed_action_tenant_id_idempotency_key"),
        ),
        sa.UniqueConstraint(
            "tenant_id", "proposal_id", name=op.f("uq_executed_action_tenant_id_proposal_id")
        ),
    )
    op.create_index(
        "ix_executed_action_case",
        "executed_action",
        ["tenant_id", "case_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_executed_action_case", table_name="executed_action")
    op.drop_table("executed_action")
    op.drop_constraint(op.f("uq_approval_tenant_id_id"), "approval", type_="unique")
