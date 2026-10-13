from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from onramp.policy.corpus import load_corpus
from onramp.policy.loader import load_documents
from tests.integration.api_support import as_user, build_client, open_case

CORPUS = Path(__file__).resolve().parents[3] / "policies"


@pytest.fixture
def client(
    onramp_sessions: sessionmaker[Session], marketplace_sessions: sessionmaker[Session]
) -> TestClient:
    client = build_client(onramp_sessions, marketplace_sessions)
    with onramp_sessions() as session, session.begin():
        load_documents(session, load_corpus(CORPUS))
    return client


def test_citation_opens_the_exact_passage(client: TestClient) -> None:
    response = client.get("/policy-passages/payout_v11_section_4_2", headers=as_user("usr_op"))

    assert response.status_code == 200
    body = response.json()
    assert (body["version"], body["section"]) == (11, "4.2")
    assert "5 calendar days" in body["text"]


def test_unknown_passage_is_404(client: TestClient) -> None:
    response = client.get("/policy-passages/payout_v99_section_1_1", headers=as_user("usr_op"))

    assert response.status_code == 404


def test_case_shows_policy_in_force_at_incident_time(client: TestClient) -> None:
    # The test marketplace's submissions are from 2026-08-20, under payout v12.
    case_id = open_case(client)

    body = client.get(f"/cases/{case_id}/policy", headers=as_user("usr_op")).json()

    (version,) = body["versions"]
    assert (version["policy_id"], version["version"]) == ("payout", 12)
    assert version["parameters"]["settlement_window_days"] == {
        "value": 7,
        "passage_id": "payout_v12_section_4_2",
    }


def test_other_tenant_cannot_see_case_policy(client: TestClient) -> None:
    case_id = open_case(client)

    assert client.get(f"/cases/{case_id}/policy", headers=as_user("usr_g")).status_code == 404
