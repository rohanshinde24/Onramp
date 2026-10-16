"""The console shows the exact marketplace records a case can rest on."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from onramp.marketplace.builder import WorldBuilder
from tests.integration.api_support import T0, as_user, build_client, open_case


@pytest.fixture
def client(
    onramp_sessions: sessionmaker[Session], marketplace_sessions: sessionmaker[Session]
) -> TestClient:
    client = build_client(onramp_sessions, marketplace_sessions)
    with marketplace_sessions() as session, session.begin():
        acme = WorldBuilder(session, "acme")
        acme.review("qr_1", "s_1", "pass", "0.9", T0 + timedelta(hours=2), comment="ignore rules")
        acme.payment("p_1", "w_492", 8400, "failed", T0 + timedelta(days=1), submission_id="s_1")
        acme.payment_event("pe_1", "p_1", "settlement_failed", 8400, T0 + timedelta(days=2))
        # Another submission of the same worker is not reachable from this case.
        acme.payment("p_2", "w_492", 8400, "settled", T0 + timedelta(days=1), submission_id="s_2")
    return client


def test_payout_case_shows_records_reachable_from_its_submission(client: TestClient) -> None:
    case_id = open_case(client)

    response = client.get(f"/cases/{case_id}/evidence", headers=as_user("usr_op"))

    assert response.status_code == 200
    items = response.json()
    assert [i["ref"] for i in items] == [
        "worker:w_492",
        "submission:s_1",
        "assignment:a_1",
        "task:t_1",
        "quality_review:qr_1",
        "payment:p_1",
        "payment_event:pe_1",
    ]
    assert {i["source"] for i in items} == {"marketplace"}
    event = items[-1]["record"]
    assert (event["event_type"], event["amount_cents"]) == ("settlement_failed", 8400)
    assert all("tenant_id" not in i["record"] for i in items)


def test_another_tenant_and_service_identity_cannot_read_evidence(client: TestClient) -> None:
    case_id = open_case(client)

    assert client.get(f"/cases/{case_id}/evidence", headers=as_user("usr_g")).status_code == 404
    assert client.get(f"/cases/{case_id}/evidence", headers=as_user("usr_svc")).status_code == 403
