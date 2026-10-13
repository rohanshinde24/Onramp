"""Seed local databases with demo tenants, users, marketplace history and open cases.

``onramp-seed`` is idempotent and refuses to run outside APP_ENV=local.
"""

import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.cases.models import CaseType
from onramp.cases.service import CaseService, IntakeRequest
from onramp.config import AppEnv, get_settings
from onramp.db.engine import make_engine, make_session_factory
from onramp.identity.models import Tenant, User
from onramp.identity.principal import Principal
from onramp.identity.roles import Role
from onramp.marketplace.reader import SqlMarketplaceReader
from onramp.marketplace.seed import seed_marketplace

TENANTS = {"acme": "Acme Tasks", "globex": "Globex Labs"}
ROLES = (Role.OPERATOR, Role.LEAD, Role.ADMIN, Role.MARKETPLACE_SERVICE)


def seed(
    onramp: sessionmaker[Session], marketplace: sessionmaker[Session], now: datetime
) -> list[str]:
    """Seed every demo tenant that does not exist yet. Returns the tenants seeded."""
    seeded = []
    cases = CaseService(onramp, SqlMarketplaceReader(marketplace), lambda: now)
    for tenant_id, name in TENANTS.items():
        with onramp() as session, session.begin():
            if session.get(Tenant, tenant_id) is not None:
                continue
            session.add(Tenant(id=tenant_id, name=name, payout_approval_limit_cents=50_000))
            session.flush()
            session.add_all(
                User(
                    id=f"{tenant_id}_{role.value}",
                    tenant_id=tenant_id,
                    email=f"{role.value}@{tenant_id}.example.test",
                    display_name=f"{name} {role.value.replace('_', ' ')}",
                    role=role.value,
                )
                for role in ROLES
            )
        with marketplace() as session, session.begin():
            seed_marketplace(session, tenant_id, now - timedelta(days=14))

        service = Principal(tenant_id, f"{tenant_id}_marketplace_service", Role.MARKETPLACE_SERVICE)
        p = f"{tenant_id}_"
        for case_type, subject, text in [
            (CaseType.PAYOUT, f"{p}s_8821", "I completed this task but did not receive my payout."),
            (CaseType.PAYOUT, f"{p}s_8822", "Where is my money for the task last week?"),
            (CaseType.QUALITY_REVIEW, f"{p}qr_4283", "The task had no timestamps requirement."),
            (CaseType.ONBOARDING, f"{p}w_610", "My account has been pending for days."),
        ]:
            cases.open_case(
                service,
                IntakeRequest(
                    case_type=case_type,
                    subject_id=subject,
                    intake_key=f"seed-{subject}",
                    description=text,
                ),
            )
        seeded.append(tenant_id)
    return seeded


def main() -> None:
    settings = get_settings()
    if settings.app_env is not AppEnv.LOCAL:
        sys.exit("onramp-seed only runs with APP_ENV=local")
    onramp = make_session_factory(make_engine(str(settings.database_url)))
    marketplace = make_session_factory(make_engine(str(settings.marketplace_database_url)))
    seeded = seed(onramp, marketplace, datetime.now(UTC))
    print(f"seeded tenants: {', '.join(seeded) or 'none (already present)'}")
    with onramp() as session:
        for user in session.scalars(select(User).order_by(User.tenant_id, User.id)):
            print(f"  {user.tenant_id:8} {user.id:30} {user.role}")
    print("mint a token with: onramp-dev-token <tenant_id> <user_id>")


if __name__ == "__main__":
    main()
