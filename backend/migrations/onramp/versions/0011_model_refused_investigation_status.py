"""model refused investigation status

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_investigation_run_known_status"), "investigation_run", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_investigation_run_known_status"),
        "investigation_run",
        "status IN ('concluded', 'budget_exhausted', 'invalid_output', 'model_error', "
        "'model_refused', 'superseded')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_investigation_run_known_status"), "investigation_run", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_investigation_run_known_status"),
        "investigation_run",
        "status IN ('concluded', 'budget_exhausted', 'invalid_output', 'model_error', "
        "'superseded')",
    )
