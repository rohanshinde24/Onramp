from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
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


def _run(client: TestClient, case_id: str, version: int = 1, user: str = "usr_op") -> Response:
    return client.post(
        f"/cases/{case_id}/checks", json={"expected_version": version}, headers=as_user(user)
    )


def test_operator_runs_checks_and_sees_the_reasoning(client: TestClient) -> None:
    case_id = open_case(client)

    response = _run(client, case_id)

    assert response.status_code == 201
    run = response.json()
    # The shared test world has no quality review for s_1, so under section 2.1 the
    # submission is not yet eligible for payment.
    assert run["outcome"] == "resolve"
    assert run["reason_codes"] == ["SUBMISSION_NOT_ELIGIBLE"]
    assert run["citations"] == ["payout_v12_section_2_1"]
    assert run["policy_version"] == 12
    case = client.get(f"/cases/{case_id}", headers=as_user("usr_op")).json()
    assert case["state"] == "proposed"
    history = client.get(f"/cases/{case_id}/checks", headers=as_user("usr_op")).json()
    assert [h["id"] for h in history] == [run["id"]]


def test_rerun_after_the_case_moved_on_conflicts(client: TestClient) -> None:
    case_id = open_case(client)
    _run(client, case_id)
    version = client.get(f"/cases/{case_id}", headers=as_user("usr_op")).json()["version"]

    response = _run(client, case_id, version)

    assert response.status_code == 409
    assert response.json()["error"] == "checks_not_applicable"


def test_stale_version_conflicts(client: TestClient) -> None:
    assert _run(client, open_case(client), version=7).status_code == 409


def test_admin_cannot_run_checks_and_other_tenant_sees_nothing(client: TestClient) -> None:
    case_id = open_case(client)

    assert _run(client, case_id, user="usr_admin").status_code == 403
    assert _run(client, case_id, user="usr_g").status_code == 404
