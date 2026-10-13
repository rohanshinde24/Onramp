"""Shared setup for API integration tests: two tenants, users per role, a small marketplace."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from onramp.api.app import create_app
from onramp.api.services import AppServices, AuthConfig
from onramp.cases.service import CaseService
from onramp.identity.models import Tenant, User
from onramp.identity.tokens import issue_token
from onramp.marketplace.builder import WorldBuilder
from onramp.marketplace.reader import SqlMarketplaceReader

AUTH = AuthConfig("integration-signing-key-0123456789abcdef", "onramp", "onramp-api")
T0 = datetime(2026, 8, 20, 9, tzinfo=UTC)

USERS = [
    ("usr_op", "acme", "operator"),
    ("usr_lead", "acme", "lead"),
    ("usr_admin", "acme", "admin"),
    ("usr_svc", "acme", "marketplace_service"),
    ("usr_g", "globex", "lead"),
]


def auth_header(user_id: str, tenant_id: str) -> dict[str, str]:
    jwt = issue_token(
        secret=AUTH.secret,
        issuer=AUTH.issuer,
        audience=AUTH.audience,
        user_id=user_id,
        tenant_id=tenant_id,
        now=datetime.now(UTC),
    )
    return {"Authorization": f"Bearer {jwt}"}


def as_user(user_id: str) -> dict[str, str]:
    tenant = next(t for u, t, _ in USERS if u == user_id)
    return auth_header(user_id, tenant)


def build_client(
    onramp_sessions: sessionmaker[Session],
    marketplace_sessions: sessionmaker[Session],
    clock: Callable[[], datetime] | None = None,
) -> TestClient:
    with onramp_sessions() as session, session.begin():
        session.add_all(
            [
                Tenant(id="acme", name="Acme", payout_approval_limit_cents=50_000),
                Tenant(id="globex", name="Globex", payout_approval_limit_cents=50_000),
            ]
        )
        session.flush()
        session.add_all(
            User(id=u, tenant_id=t, email=f"{u}@{t}.test", display_name=u, role=r)
            for u, t, r in USERS
        )
    with marketplace_sessions() as session, session.begin():
        acme = WorldBuilder(session, "acme").tenant()
        acme.worker("w_492", "Sam Lee")
        for i in range(1, 4):
            acme.task(f"t_{i}", 8400)
            acme.assignment(f"a_{i}", f"t_{i}", "w_492", T0)
            acme.submission(f"s_{i}", f"a_{i}", T0 + timedelta(hours=i))
        globex = WorldBuilder(session, "globex").tenant()
        globex.worker("w_g", "Lee Sam")
        globex.task("t_g", 100)
        globex.assignment("a_g", "t_g", "w_g", T0)
        globex.submission("s_g", "a_g", T0)

    reader = SqlMarketplaceReader(marketplace_sessions)
    cases = (
        CaseService(onramp_sessions, reader, clock)
        if clock
        else CaseService(onramp_sessions, reader)
    )
    return TestClient(create_app(AppServices(sessions=onramp_sessions, auth=AUTH, cases=cases)))
