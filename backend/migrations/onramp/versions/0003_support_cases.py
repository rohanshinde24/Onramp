"""support cases

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "support_case",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("case_type", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=64), nullable=False),
        sa.Column("incident_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("incident_basis", sa.String(length=64), nullable=False),
        sa.Column("intake_key", sa.String(length=200), nullable=False),
        sa.Column("reported_description", sa.Text(), nullable=False),
        sa.Column("opened_by_type", sa.String(length=32), nullable=False),
        sa.Column("opened_by_id", sa.String(length=64), nullable=False),
        sa.Column("assignee_id", sa.String(length=64), nullable=True),
        sa.Column("resolution_code", sa.String(length=64), nullable=True),
        sa.Column("escalation_reason", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(case_type = 'payout' AND subject_type = 'submission') OR (case_type = 'onboarding' AND subject_type = 'worker') OR (case_type = 'quality_review' AND subject_type = 'quality_review')",
            name=op.f("ck_support_case_subject_matches_case_type"),
        ),
        sa.CheckConstraint(
            "case_type IN ('payout', 'onboarding', 'quality_review')",
            name=op.f("ck_support_case_known_case_type"),
        ),
        sa.CheckConstraint(
            "state IN ('created', 'initial_checks', 'investigating', 'waiting_for_evidence', 'waiting_for_settlement', 'proposed', 'awaiting_approval', 'executing', 'verifying', 'escalated', 'resolved', 'closed_no_action')",
            name=op.f("ck_support_case_known_state"),
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_support_case_positive_version")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "assignee_id"],
            ["app_user.tenant_id", "app_user.id"],
            name=op.f("fk_support_case_tenant_id_assignee_id_app_user"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_support_case_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_case")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_support_case_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "intake_key", name=op.f("uq_support_case_tenant_id_intake_key")
        ),
    )
    op.create_index(
        "ix_support_case_inbox", "support_case", ["tenant_id", "state", "created_at"], unique=False
    )
    op.create_index(
        "uq_support_case_one_open_per_subject",
        "support_case",
        ["tenant_id", "case_type", "subject_id"],
        unique=True,
        postgresql_where=sa.text("state NOT IN ('resolved', 'closed_no_action')"),
    )
    op.create_index(
        "ix_audit_event_subject",
        "audit_event",
        ["tenant_id", "subject_type", "subject_id", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_event_subject", table_name="audit_event")
    op.drop_index(
        "uq_support_case_one_open_per_subject",
        table_name="support_case",
        postgresql_where=sa.text("state NOT IN ('resolved', 'closed_no_action')"),
    )
    op.drop_index("ix_support_case_inbox", table_name="support_case")
    op.drop_table("support_case")
