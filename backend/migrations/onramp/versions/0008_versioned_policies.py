"""versioned policies

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # GiST needs btree_gist to combine equality on policy_id with range overlap.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "policy",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("domain", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy")),
    )
    op.create_table(
        "policy_version",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "effective_period",
            postgresql.TSTZRANGE(),
            sa.Computed("tstzrange(effective_from, effective_until, '[)')", persisted=True),
            nullable=False,
        ),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "loaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        postgresql.ExcludeConstraint(
            (sa.column("policy_id"), "="),
            (sa.column("effective_period"), "&&"),
            using="gist",
            name="no_overlapping_periods",
        ),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name=op.f("ck_policy_version_non_empty_period"),
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_policy_version_positive_version")),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["policy.id"], name=op.f("fk_policy_version_policy_id_policy")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy_version")),
        sa.UniqueConstraint(
            "policy_id", "version", name=op.f("uq_policy_version_policy_id_version")
        ),
    )
    op.create_table(
        "policy_passage",
        sa.Column("passage_id", sa.String(length=96), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(length=16), nullable=False),
        sa.Column("heading", sa.String(length=200), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["policy_id", "version"],
            ["policy_version.policy_id", "policy_version.version"],
            name=op.f("fk_policy_passage_policy_id_version_policy_version"),
        ),
        sa.PrimaryKeyConstraint("passage_id", name=op.f("pk_policy_passage")),
        sa.UniqueConstraint(
            "policy_id",
            "version",
            "section",
            name=op.f("uq_policy_passage_policy_id_version_section"),
        ),
    )


def downgrade() -> None:
    op.drop_table("policy_passage")
    op.drop_table("policy_version")
    op.drop_table("policy")
