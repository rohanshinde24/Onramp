"""approvals

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("proposal_id", sa.String(length=64), nullable=False),
        sa.Column("approver_id", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("risk_class", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')", name=op.f("ck_approval_known_decision")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "approver_id"],
            ["app_user.tenant_id", "app_user.id"],
            name=op.f("fk_approval_tenant_id_approver_id_app_user"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "proposal_id"],
            ["proposal.tenant_id", "proposal.id"],
            name=op.f("fk_approval_tenant_id_proposal_id_proposal"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_approval_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval")),
        sa.UniqueConstraint(
            "tenant_id", "proposal_id", name=op.f("uq_approval_tenant_id_proposal_id")
        ),
    )


def downgrade() -> None:
    op.drop_table("approval")
