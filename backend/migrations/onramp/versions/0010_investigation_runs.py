"""investigation runs

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "investigation_run",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("case_version", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("conclusion", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("problems", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_calls", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("elapsed_s", sa.Float(), nullable=False),
        sa.Column("trajectory", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("proposal_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('concluded', 'budget_exhausted', 'invalid_output', 'model_error', 'superseded')",
            name=op.f("ck_investigation_run_known_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["support_case.tenant_id", "support_case.id"],
            name=op.f("fk_investigation_run_tenant_id_case_id_support_case"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_investigation_run_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_investigation_run")),
    )
    op.create_index(
        "ix_investigation_run_case",
        "investigation_run",
        ["tenant_id", "case_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_investigation_run_case", table_name="investigation_run")
    op.drop_table("investigation_run")
