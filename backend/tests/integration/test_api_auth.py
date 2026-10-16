import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from onramp.identity.models import User
from tests.integration.api_support import as_user, auth_header, build_client


@pytest.fixture
def client(
    onramp_sessions: sessionmaker[Session], marketplace_sessions: sessionmaker[Session]
) -> TestClient:
    return build_client(onramp_sessions, marketplace_sessions)


def test_me_returns_principal_with_role_from_database(client: TestClient) -> None:
    response = client.get("/me", headers=as_user("usr_op"))

    assert response.status_code == 200
    body = response.json()
    assert (body["user_id"], body["tenant_id"], body["role"]) == ("usr_op", "acme", "operator")
    assert "approve.high_risk" not in body["permissions"]
    assert (body["display_name"], body["tenant_name"]) == ("usr_op", "Acme")
    assert body["payout_approval_limit_cents"] == 50_000


def test_missing_token_is_unauthenticated(client: TestClient) -> None:
    assert client.get("/me").status_code == 401


def test_token_pairing_user_with_wrong_tenant_is_rejected(client: TestClient) -> None:
    # usr_g exists, but in globex. Claiming acme must not find them.
    assert client.get("/me", headers=auth_header("usr_g", "acme")).status_code == 401


def test_role_change_takes_effect_without_new_token(
    client: TestClient, onramp_sessions: sessionmaker[Session]
) -> None:
    headers = as_user("usr_op")
    with onramp_sessions() as session, session.begin():
        session.execute(update(User).filter_by(id="usr_op").values(role="lead"))

    assert client.get("/me", headers=headers).json()["role"] == "lead"
