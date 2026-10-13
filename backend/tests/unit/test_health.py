from fastapi.testclient import TestClient

from onramp.api.app import create_app
from onramp.api.health import ReadinessCheck


async def _ok() -> None:
    return None


async def _down() -> None:
    raise ConnectionRefusedError


def test_liveness_ignores_dependencies() -> None:
    client = TestClient(create_app([ReadinessCheck("database", _down)]))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_all_checks_pass() -> None:
    client = TestClient(create_app([ReadinessCheck("database", _ok)]))

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "ready": True,
        "checks": [{"name": "database", "ok": True, "error": None}],
    }


def test_not_ready_names_the_failing_check() -> None:
    client = TestClient(
        create_app([ReadinessCheck("database", _ok), ReadinessCheck("search", _down)])
    )

    response = client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["checks"][1] == {
        "name": "search",
        "ok": False,
        "error": "ConnectionRefusedError",
    }
