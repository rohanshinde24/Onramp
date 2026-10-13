from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine

from onramp.api.app import create_app
from onramp.db.readiness import database_check


def test_ready_against_live_database(onramp_engine: Engine) -> None:
    client = TestClient(create_app([database_check("onramp_db", onramp_engine)]))

    response = client.get("/readyz")

    assert response.status_code == 200


def test_not_ready_when_database_unreachable() -> None:
    dead = create_engine(
        "postgresql+psycopg://onramp:x@127.0.0.1:1/onramp", connect_args={"connect_timeout": 1}
    )
    client = TestClient(create_app([database_check("onramp_db", dead)]))

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"][0]["name"] == "onramp_db"
