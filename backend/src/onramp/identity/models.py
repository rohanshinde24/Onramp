from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from onramp.db.base import OnrampBase
from onramp.db.types import created_at_column, id_column
from onramp.identity.roles import Role

_ROLE_VALUES = ", ".join(f"'{role.value}'" for role in Role)


class Tenant(OnrampBase):
    __tablename__ = "tenant"
    __table_args__ = (
        CheckConstraint("payout_approval_limit_cents > 0", name="positive_payout_limit"),
    )

    id: Mapped[str] = id_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Highest PAYOUT_ADJUSTMENT a lead may approve. Larger amounts escalate.
    payout_approval_limit_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class User(OnrampBase):
    __tablename__ = "app_user"
    __table_args__ = (
        # Target of composite foreign keys, so a row can only reference a user of its own tenant.
        UniqueConstraint("tenant_id", "id", name="uq_app_user_tenant_id_id"),
        UniqueConstraint("tenant_id", "email"),
        CheckConstraint(f"role IN ({_ROLE_VALUES})", name="known_role"),
    )

    id: Mapped[str] = id_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = created_at_column()
