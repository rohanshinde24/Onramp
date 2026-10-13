from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.api_support import as_user, build_client


@pytest.fixture
def client(
    onramp_sessions: sessionmaker[Session], marketplace_sessions: sessionmaker[Session]
) -> TestClient:
    return build_client(onramp_sessions, marketplace_sessions)


def _intake(subject_id: str = "s_1", key: str = "ticket-1", **extra: Any) -> dict[str, Any]:
    return {
        "case_type": "payout",
        "subject_id": subject_id,
        "intake_key": key,
        "description": "Not paid for this task",
        **extra,
    }


def _open(client: TestClient, user: str = "usr_svc", **kwargs: Any) -> dict[str, Any]:
    response = client.post("/cases", json=_intake(**kwargs), headers=as_user(user))
    assert response.status_code in (200, 201), response.text
    case: dict[str, Any] = response.json()["case"]
    return case


def test_marketplace_service_opens_a_case(client: TestClient) -> None:
    response = client.post("/cases", json=_intake(), headers=as_user("usr_svc"))

    assert response.status_code == 201
    body = response.json()
    assert body["created"] is True
    assert body["case"]["state"] == "created"
    assert body["case"]["worker_id"] == "w_492"
    assert body["case"]["subject"] == "submission:s_1"


def test_repeat_intake_returns_200_and_same_case(client: TestClient) -> None:
    first = _open(client)
    response = client.post("/cases", json=_intake(), headers=as_user("usr_svc"))

    assert response.status_code == 200
    assert response.json()["case"]["id"] == first["id"]


@pytest.mark.parametrize("field", ["tenant_id", "worker_id"])
def test_body_cannot_supply_tenant_or_worker(client: TestClient, field: str) -> None:
    response = client.post(
        "/cases", json=_intake(**{field: "anything"}), headers=as_user("usr_svc")
    )

    assert response.status_code == 422


def test_case_tenant_and_worker_come_from_server(client: TestClient) -> None:
    case = _open(client)

    assert case["worker_id"] == "w_492"
    assert client.get(f"/cases/{case['id']}", headers=as_user("usr_g")).status_code == 404


def test_other_tenants_subject_is_404(client: TestClient) -> None:
    response = client.post("/cases", json=_intake("s_g"), headers=as_user("usr_svc"))

    assert response.status_code == 404
    assert response.json()["error"] == "subject_not_found"


def test_case_in_another_tenant_reads_as_not_found(client: TestClient) -> None:
    case = _open(client)

    for path in (f"/cases/{case['id']}", f"/cases/{case['id']}/timeline"):
        response = client.get(path, headers=as_user("usr_g"))
        assert response.status_code == 404
        assert response.json()["error"] == "case_not_found"


def test_inbox_lists_only_own_tenant_newest_first_with_paging(client: TestClient) -> None:
    ids = [_open(client, subject_id=f"s_{i}", key=f"k{i}")["id"] for i in (1, 2, 3)]

    page1 = client.get("/cases", params={"limit": 2}, headers=as_user("usr_op")).json()
    page2 = client.get(
        "/cases", params={"limit": 2, "before": page1["next_before"]}, headers=as_user("usr_op")
    ).json()

    listed = [c["id"] for c in page1["cases"] + page2["cases"]]
    assert sorted(listed) == sorted(ids)
    assert len(set(listed)) == 3
    assert page2["next_before"] is None
    other = client.get("/cases", headers=as_user("usr_g")).json()
    assert other["cases"] == []


def test_inbox_filters_by_state(client: TestClient) -> None:
    case = _open(client)
    client.post(
        f"/cases/{case['id']}/escalate",
        json={"reason": "operator_judgment", "expected_version": case["version"]},
        headers=as_user("usr_op"),
    )

    escalated = client.get(
        "/cases", params={"state": "escalated"}, headers=as_user("usr_op")
    ).json()
    created = client.get("/cases", params={"state": "created"}, headers=as_user("usr_op")).json()

    assert [c["id"] for c in escalated["cases"]] == [case["id"]]
    assert created["cases"] == []


def test_escalation_is_audited_on_the_timeline(client: TestClient) -> None:
    case = _open(client)

    response = client.post(
        f"/cases/{case['id']}/escalate",
        json={"reason": "conflicting_evidence", "expected_version": 1, "note": "two payments"},
        headers=as_user("usr_op"),
    )

    assert response.status_code == 200
    assert response.json()["escalation_reason"] == "conflicting_evidence"
    timeline = client.get(f"/cases/{case['id']}/timeline", headers=as_user("usr_op")).json()
    assert [e["action"] for e in timeline] == ["case.created", "case.transitioned"]
    assert timeline[1]["details"]["to"] == "escalated"
    assert timeline[1]["actor_id"] == "usr_op"


def test_escalation_with_stale_version_conflicts(client: TestClient) -> None:
    case = _open(client)
    body = {"reason": "operator_judgment", "expected_version": 1}
    client.post(f"/cases/{case['id']}/escalate", json=body, headers=as_user("usr_op"))

    response = client.post(f"/cases/{case['id']}/escalate", json=body, headers=as_user("usr_op"))

    assert response.status_code == 409


def test_admin_cannot_escalate_and_service_cannot_read(client: TestClient) -> None:
    case = _open(client)
    body = {"reason": "operator_judgment", "expected_version": 1}

    escalate = client.post(f"/cases/{case['id']}/escalate", json=body, headers=as_user("usr_admin"))
    read = client.get(f"/cases/{case['id']}", headers=as_user("usr_svc"))

    assert escalate.status_code == 403
    assert read.status_code == 403


def test_unknown_escalation_reason_is_rejected(client: TestClient) -> None:
    case = _open(client)

    response = client.post(
        f"/cases/{case['id']}/escalate",
        json={"reason": "because", "expected_version": 1},
        headers=as_user("usr_op"),
    )

    assert response.status_code == 422
