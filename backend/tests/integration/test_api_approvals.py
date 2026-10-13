from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from onramp.cases.state import CaseState
from onramp.identity.models import Tenant
from tests.integration.api_support import as_user, build_client, move_case, open_case

EVIDENCE = ["submission:s_1", "payment_event:pe_1"]


@pytest.fixture
def client(
    onramp_sessions: sessionmaker[Session], marketplace_sessions: sessionmaker[Session]
) -> TestClient:
    return build_client(onramp_sessions, marketplace_sessions)


def _awaiting(
    client: TestClient,
    sessions: sessionmaker[Session],
    proposer: str = "usr_op",
    amount: int = 8400,
) -> dict[str, Any]:
    case_id = open_case(client)
    version = move_case(sessions, case_id, CaseState.INITIAL_CHECKS, CaseState.INVESTIGATING)
    response = client.post(
        f"/cases/{case_id}/proposals",
        json={
            "expected_version": version,
            "proposal": {
                "resolution_code": "MISSING_PAYOUT",
                "action_type": "PAYOUT_ADJUSTMENT",
                "action_params": {"amount_cents": amount, "currency": "USD"},
                "evidence_refs": EVIDENCE,
                "rationale": "settlement failed",
            },
        },
        headers=as_user(proposer),
    )
    assert response.status_code == 201, response.text
    proposal: dict[str, Any] = response.json()
    return proposal


def _decide(
    client: TestClient,
    proposal: dict[str, Any],
    user: str,
    decision: str = "approved",
    content_hash: str | None = None,
) -> Any:
    return client.post(
        f"/proposals/{proposal['id']}/approval",
        json={"decision": decision, "content_hash": content_hash or proposal["content_hash"]},
        headers=as_user(user),
    )


def _state(client: TestClient, case_id: str) -> str:
    state: str = client.get(f"/cases/{case_id}", headers=as_user("usr_op")).json()["state"]
    return state


def test_lead_approval_moves_case_to_executing(
    client: TestClient, onramp_sessions: sessionmaker[Session]
) -> None:
    proposal = _awaiting(client, onramp_sessions)

    response = _decide(client, proposal, "usr_lead")

    assert response.status_code == 201
    assert response.json()["risk_class"] == "high"
    assert _state(client, proposal["case_id"]) == "executing"
    timeline = client.get(
        f"/cases/{proposal['case_id']}/timeline", headers=as_user("usr_op")
    ).json()
    assert "approval.approved" in [e["action"] for e in timeline]


def test_operator_cannot_approve_high_risk(
    client: TestClient, onramp_sessions: sessionmaker[Session]
) -> None:
    proposal = _awaiting(client, onramp_sessions, proposer="usr_lead")

    assert _decide(client, proposal, "usr_op").status_code == 403
    assert _state(client, proposal["case_id"]) == "awaiting_approval"


def test_proposer_cannot_approve_their_own_action(
    client: TestClient, onramp_sessions: sessionmaker[Session]
) -> None:
    proposal = _awaiting(client, onramp_sessions, proposer="usr_lead")

    response = _decide(client, proposal, "usr_lead")

    assert response.status_code == 403
    assert response.json()["error"] == "self_approval"


def test_approval_must_match_proposal_content(
    client: TestClient, onramp_sessions: sessionmaker[Session]
) -> None:
    proposal = _awaiting(client, onramp_sessions)

    response = _decide(client, proposal, "usr_lead", content_hash="0" * 64)

    assert response.status_code == 409
    assert response.json()["error"] == "content_mismatch"
    assert _state(client, proposal["case_id"]) == "awaiting_approval"


def test_replayed_approval_is_rejected(
    client: TestClient, onramp_sessions: sessionmaker[Session]
) -> None:
    proposal = _awaiting(client, onramp_sessions)
    assert _decide(client, proposal, "usr_lead").status_code == 201

    replay = _decide(client, proposal, "usr_lead")

    assert replay.status_code == 409
    assert replay.json()["error"] == "already_decided"


def test_rejection_returns_case_to_investigation(
    client: TestClient, onramp_sessions: sessionmaker[Session]
) -> None:
    proposal = _awaiting(client, onramp_sessions)

    response = _decide(client, proposal, "usr_lead", decision="rejected")

    assert response.status_code == 201
    assert _state(client, proposal["case_id"]) == "investigating"


def test_limit_lowered_after_proposal_escalates_on_approval(
    client: TestClient, onramp_sessions: sessionmaker[Session]
) -> None:
    proposal = _awaiting(client, onramp_sessions)
    with onramp_sessions() as session, session.begin():
        session.execute(update(Tenant).filter_by(id="acme").values(payout_approval_limit_cents=100))

    assert _decide(client, proposal, "usr_lead").status_code == 201
    assert _state(client, proposal["case_id"]) == "escalated"


def test_other_tenant_cannot_approve(
    client: TestClient, onramp_sessions: sessionmaker[Session]
) -> None:
    proposal = _awaiting(client, onramp_sessions)

    assert _decide(client, proposal, "usr_g").status_code == 404


def test_concurrent_approvals_record_exactly_one(
    onramp_committed: sessionmaker[Session], marketplace_committed: sessionmaker[Session]
) -> None:
    client = build_client(onramp_committed, marketplace_committed)
    proposal = _awaiting(client, onramp_committed)

    with ThreadPoolExecutor(max_workers=6) as pool:
        codes = list(
            pool.map(lambda _: _decide(client, proposal, "usr_lead").status_code, range(6))
        )

    assert codes.count(201) == 1
    assert set(codes) == {201, 409}
