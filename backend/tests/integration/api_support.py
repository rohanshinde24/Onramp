"""Shared setup for API integration tests: two tenants, users per role, a small marketplace."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from onramp.actions.evidence import EvidenceRequester
from onramp.api.app import create_app
from onramp.api.services import AppServices, AuthConfig
from onramp.approvals.service import ApprovalService
from onramp.audit.service import Actor
from onramp.cases.models import SupportCase
from onramp.cases.service import CaseService, transition
from onramp.cases.state import CaseState
from onramp.checks.service import CheckService
from onramp.identity.models import Tenant, User
from onramp.identity.tokens import issue_token
from onramp.marketplace.builder import WorldBuilder
from onramp.marketplace.reader import SqlMarketplaceReader
from onramp.marketplace.writer import SqlMarketplaceWriter
from onramp.proposals.service import ProposalService

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
    now = clock or (lambda: datetime.now(UTC))
    services = AppServices(
        sessions=onramp_sessions,
        auth=AUTH,
        cases=CaseService(onramp_sessions, reader, now),
        proposals=ProposalService(onramp_sessions, now),
        approvals=ApprovalService(onramp_sessions, now),
        checks=CheckService(
            onramp_sessions,
            reader,
            EvidenceRequester(SqlMarketplaceWriter(marketplace_sessions, clock=now)),
            now,
        ),
    )
    return TestClient(create_app(services))


def open_case(client: TestClient, subject_id: str = "s_1", key: str = "ticket-1") -> str:
    response = client.post(
        "/cases",
        json={
            "case_type": "payout",
            "subject_id": subject_id,
            "intake_key": key,
            "description": "Not paid",
        },
        headers=as_user("usr_svc"),
    )
    assert response.status_code in (200, 201), response.text
    case_id: str = response.json()["case"]["id"]
    return case_id


def move_case(sessions: sessionmaker[Session], case_id: str, *states: CaseState) -> int:
    """Drive a case through states with the real transition helper; returns the new version.

    Stands in for the checks and workflow that later phases add.
    """
    with sessions() as session, session.begin():
        case = session.get_one(SupportCase, case_id)
        for state in states:
            transition(
                session,
                case,
                state,
                actor=Actor.system(),
                occurred_at=datetime.now(UTC),
                reason="test_setup",
            )
        return case.version
