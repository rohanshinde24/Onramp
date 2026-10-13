from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from onramp.api.app import create_app
from onramp.api.services import AppServices, AuthConfig
from onramp.cases.service import CaseService
from onramp.identity.models import Tenant, User
from onramp.identity.tokens import issue_token
from onramp.marketplace.reader import SqlMarketplaceReader

AUTH = AuthConfig("integration-signing-key-0123456789abcdef", "onramp", "onramp-api")


def token(user_id: str, tenant_id: str) -> dict[str, str]:
    jwt = issue_token(
        secret=AUTH.secret,
        issuer=AUTH.issuer,
        audience=AUTH.audience,
        user_id=user_id,
        tenant_id=tenant_id,
        now=datetime.now(UTC),
    )
    return {"Authorization": f"Bearer {jwt}"}


@pytest.fixture
def client(
    onramp_sessions: sessionmaker[Session], marketplace_sessions: sessionmaker[Session]
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
            [
                User(
                    id="usr_op",
                    tenant_id="acme",
                    email="op@acme.test",
                    display_name="Op",
                    role="operator",
                ),
                User(
                    id="usr_g",
                    tenant_id="globex",
                    email="g@globex.test",
                    display_name="G",
                    role="lead",
                ),
            ]
        )
    services = AppServices(
        sessions=onramp_sessions,
        auth=AUTH,
        cases=CaseService(onramp_sessions, SqlMarketplaceReader(marketplace_sessions)),
    )
    return TestClient(create_app(services))


def test_me_returns_principal_with_role_from_database(client: TestClient) -> None:
    response = client.get("/me", headers=token("usr_op", "acme"))

    assert response.status_code == 200
    body = response.json()
    assert (body["user_id"], body["tenant_id"], body["role"]) == ("usr_op", "acme", "operator")
    assert "approve.high_risk" not in body["permissions"]


def test_missing_token_is_unauthenticated(client: TestClient) -> None:
    assert client.get("/me").status_code == 401


def test_token_pairing_user_with_wrong_tenant_is_rejected(client: TestClient) -> None:
    # usr_g exists, but in globex. Claiming acme must not find them.
    assert client.get("/me", headers=token("usr_g", "acme")).status_code == 401


def test_role_change_takes_effect_without_new_token(
    client: TestClient, onramp_sessions: sessionmaker[Session]
) -> None:
    headers = token("usr_op", "acme")
    with onramp_sessions() as session, session.begin():
        session.execute(update(User).filter_by(id="usr_op").values(role="lead"))

    assert client.get("/me", headers=headers).json()["role"] == "lead"
