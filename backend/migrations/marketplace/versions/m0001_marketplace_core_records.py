"""marketplace core records

Revision ID: m0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "m0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant")),
    )
    op.create_table(
        "task",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("payout_cents", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("payout_cents > 0", name=op.f("ck_task_positive_payout")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_task_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_task_tenant_id_id"),
    )
    op.create_table(
        "worker",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending_onboarding', 'active', 'suspended', 'denied')",
            name=op.f("ck_worker_known_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_worker_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_worker")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_worker_tenant_id_id"),
    )
    op.create_table(
        "write_request",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_write_request_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_write_request")),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name=op.f("uq_write_request_tenant_id_idempotency_key")
        ),
    )
    op.create_table(
        "assignment",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('assigned', 'submitted', 'expired')",
            name=op.f("ck_assignment_known_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["task.tenant_id", "task.id"],
            name=op.f("fk_assignment_tenant_id_task_id_task"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "worker_id"],
            ["worker.tenant_id", "worker.id"],
            name=op.f("fk_assignment_tenant_id_worker_id_worker"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_assignment_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assignment")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_assignment_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "task_id",
            "worker_id",
            name=op.f("uq_assignment_tenant_id_task_id_worker_id"),
        ),
    )
    op.create_table(
        "document",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "worker_id"],
            ["worker.tenant_id", "worker.id"],
            name=op.f("fk_document_tenant_id_worker_id_worker"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_document_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_document_tenant_id_id"),
    )
    op.create_table(
        "verification_record",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('identity', 'tax_form', 'skills_assessment')",
            name=op.f("ck_verification_record_known_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'passed', 'failed', 'expired')",
            name=op.f("ck_verification_record_known_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "worker_id"],
            ["worker.tenant_id", "worker.id"],
            name=op.f("fk_verification_record_tenant_id_worker_id_worker"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_verification_record_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_record")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_verification_record_tenant_id_id"),
    )
    op.create_table(
        "submission",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("assignment_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending_review', 'accepted', 'rejected')",
            name=op.f("ck_submission_known_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "assignment_id"],
            ["assignment.tenant_id", "assignment.id"],
            name=op.f("fk_submission_tenant_id_assignment_id_assignment"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_submission_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submission")),
        sa.UniqueConstraint(
            "tenant_id", "assignment_id", name=op.f("uq_submission_tenant_id_assignment_id")
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_submission_tenant_id_id"),
    )
    op.create_table(
        "payment",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=64), nullable=False),
        sa.Column("submission_id", sa.String(length=64), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(kind = 'task_payout') = (submission_id IS NOT NULL)",
            name=op.f("ck_payment_task_payout_has_submission"),
        ),
        sa.CheckConstraint(
            "kind IN ('task_payout', 'adjustment')", name=op.f("ck_payment_known_kind")
        ),
        sa.CheckConstraint(
            "status IN ('authorized', 'settled', 'failed')", name=op.f("ck_payment_known_status")
        ),
        sa.CheckConstraint("amount_cents > 0", name=op.f("ck_payment_positive_amount")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "submission_id"],
            ["submission.tenant_id", "submission.id"],
            name=op.f("fk_payment_tenant_id_submission_id_submission"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "worker_id"],
            ["worker.tenant_id", "worker.id"],
            name=op.f("fk_payment_tenant_id_worker_id_worker"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_payment_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_payment_tenant_id_id"),
    )
    op.create_table(
        "quality_review",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("submission_id", sa.String(length=64), nullable=False),
        sa.Column("reviewer_id", sa.String(length=64), nullable=False),
        sa.Column("result", sa.String(length=8), nullable=False),
        sa.Column("score", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column("rubric_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reviewer_comment", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("overturned_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "result IN ('pass', 'fail')", name=op.f("ck_quality_review_known_result")
        ),
        sa.CheckConstraint(
            "score >= 0 AND score <= 1", name=op.f("ck_quality_review_score_in_unit_range")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "submission_id"],
            ["submission.tenant_id", "submission.id"],
            name=op.f("fk_quality_review_tenant_id_submission_id_submission"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_quality_review_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_review")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_quality_review_tenant_id_id"),
    )
    op.create_table(
        "payment_event",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("payment_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("failure_reason", sa.String(length=200), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('authorized', 'settlement_succeeded', 'settlement_failed')",
            name=op.f("ck_payment_event_known_event_type"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "payment_id"],
            ["payment.tenant_id", "payment.id"],
            name=op.f("fk_payment_event_tenant_id_payment_id_payment"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_payment_event_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_event")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_payment_event_tenant_id_id"),
    )


def downgrade() -> None:
    op.drop_table("payment_event")
    op.drop_table("quality_review")
    op.drop_table("payment")
    op.drop_table("submission")
    op.drop_table("verification_record")
    op.drop_table("document")
    op.drop_table("assignment")
    op.drop_table("write_request")
    op.drop_table("worker")
    op.drop_table("task")
    op.drop_table("tenant")
