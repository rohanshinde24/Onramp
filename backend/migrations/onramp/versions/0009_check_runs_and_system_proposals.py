"""check runs and system proposals

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "check_run",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("case_version", sa.Integer(), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("findings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("missing_items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("wait_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("proposal_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('resolve', 'wait_for_settlement', 'wait_for_evidence', 'escalate', 'investigate', 'error')",
            name=op.f("ck_check_run_known_outcome"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["support_case.tenant_id", "support_case.id"],
            name=op.f("fk_check_run_tenant_id_case_id_support_case"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_check_run_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_check_run")),
    )
    # Autogenerate does not compare CHECK constraints; widen this one by hand.
    op.drop_constraint(op.f("ck_proposal_known_proposer_type"), "proposal", type_="check")
    op.create_check_constraint(
        op.f("ck_proposal_known_proposer_type"),
        "proposal",
        "proposer_type IN ('user', 'agent', 'system')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_proposal_known_proposer_type"), "proposal", type_="check")
    op.create_check_constraint(
        op.f("ck_proposal_known_proposer_type"), "proposal", "proposer_type IN ('user', 'agent')"
    )
    op.drop_table("check_run")
