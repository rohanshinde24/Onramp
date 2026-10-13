"""document requests

Revision ID: m0002
Revises: m0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "m0002"
down_revision: str | None = "m0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_request",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=64), nullable=False),
        sa.Column("document_kind", sa.String(length=64), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "worker_id"],
            ["worker.tenant_id", "worker.id"],
            name=op.f("fk_document_request_tenant_id_worker_id_worker"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_document_request_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_request")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_document_request_tenant_id_id"),
    )


def downgrade() -> None:
    op.drop_table("document_request")
