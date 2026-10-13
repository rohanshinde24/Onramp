from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from onramp.audit.service import Actor
from onramp.cases.models import SupportCase
from onramp.cases.resolutions import ActionType, ResolutionCode
from onramp.cases.state import CaseState
from onramp.proposals.content import ProposalRequest
from onramp.proposals.service import create_proposal
from tests.integration.api_support import as_user, build_client, move_case, open_case

INVESTIGATING = (CaseState.INITIAL_CHECKS, CaseState.INVESTIGATING)
EVIDENCE = ["submission:s_1", "payment_event:pe_1", "policy_passage:payout_v12_section_4_3"]


@pytest.fixture
def client(
    onramp_sessions: sessionmaker[Session], marketplace_sessions: sessionmaker[Session]
) -> TestClient:
    return build_client(onramp_sessions, marketplace_sessions)


@pytest.fixture
def case(client: TestClient, onramp_sessions: sessionmaker[Session]) -> tuple[str, int]:
    case_id = open_case(client)
    return case_id, move_case(onramp_sessions, case_id, *INVESTIGATING)


def _proposal(**overrides: Any) -> dict[str, Any]:
    proposal: dict[str, Any] = {
        "resolution_code": "MISSING_PAYOUT",
        "action_type": "PAYOUT_ADJUSTMENT",
        "action_params": {"amount_cents": 8400, "currency": "USD"},
        "reason_codes": ["MISSING_SETTLEMENT"],
        "evidence_refs": EVIDENCE,
        "rationale": "Accepted work, authorized payment, settlement failed and never retried.",
    }
    proposal.update(overrides)
    return proposal


def _propose(
    client: TestClient, case: tuple[str, int], user: str = "usr_op", **overrides: Any
) -> Any:
    case_id, version = case
    return client.post(
        f"/cases/{case_id}/proposals",
        json={"expected_version": version, "proposal": _proposal(**overrides)},
        headers=as_user(user),
    )


def _state(client: TestClient, case_id: str) -> dict[str, Any]:
    body: dict[str, Any] = client.get(f"/cases/{case_id}", headers=as_user("usr_op")).json()
    return body


def test_no_action_resolution_resolves_the_case(client: TestClient, case: tuple[str, int]) -> None:
    response = _propose(
        client, case, resolution_code="PAID_IN_FULL", action_type=None, action_params={}
    )

    assert response.status_code == 201
    assert response.json()["status"] == "accepted"
    current = _state(client, case[0])
    assert current["state"] == "resolved"
    assert current["resolution_code"] == "PAID_IN_FULL"


def test_action_resolution_waits_for_approval(client: TestClient, case: tuple[str, int]) -> None:
    response = _propose(client, case)

    assert response.status_code == 201
    assert response.json()["risk_class"] == "high"
    assert len(response.json()["content_hash"]) == 64
    assert _state(client, case[0])["state"] == "awaiting_approval"


@pytest.mark.parametrize(
    "overrides",
    [
        {"action_type": None, "action_params": {}},  # MISSING_PAYOUT needs an adjustment
        {"resolution_code": "PAID_IN_FULL"},  # PAID_IN_FULL forbids one
        {
            "resolution_code": "REVIEW_OVERTURNED",
            "action_type": "OVERTURN_QUALITY_REVIEW",
            "action_params": {},
        },  # wrong case type
        {"action_params": {"amount_cents": 8400, "currency": "USD", "worker_id": "w_g"}},
        {"action_params": {"amount_cents": -5, "currency": "USD"}},
    ],
    ids=[
        "missing-action",
        "forbidden-action",
        "wrong-case-type",
        "target-in-params",
        "negative-amount",
    ],
)
def test_invalid_proposals_are_rejected_and_change_nothing(
    client: TestClient, case: tuple[str, int], overrides: dict[str, Any]
) -> None:
    response = _propose(client, case, **overrides)

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_proposal"
    assert _state(client, case[0])["state"] == "investigating"
    assert client.get(f"/cases/{case[0]}/proposals", headers=as_user("usr_op")).json() == []


@pytest.mark.parametrize("refs", [["payment_event"], ["secret_table:x"], ["payment:../../etc"], []])
def test_malformed_evidence_refs_are_rejected(
    client: TestClient, case: tuple[str, int], refs: list[str]
) -> None:
    assert _propose(client, case, evidence_refs=refs).status_code == 422


def test_amount_over_tenant_limit_escalates(client: TestClient, case: tuple[str, int]) -> None:
    response = _propose(client, case, action_params={"amount_cents": 60_000, "currency": "USD"})

    assert response.status_code == 201
    current = _state(client, case[0])
    assert current["state"] == "escalated"
    assert current["escalation_reason"] == "amount_over_limit"


def test_cannot_propose_before_investigation(client: TestClient) -> None:
    case_id = open_case(client, subject_id="s_2", key="t2")

    response = _propose(client, (case_id, 1))

    assert response.status_code == 409
    assert response.json()["error"] == "illegal_transition"


def test_stale_version_conflicts(client: TestClient, case: tuple[str, int]) -> None:
    assert _propose(client, (case[0], case[1] - 1)).status_code == 409


def test_only_leads_propose_on_escalated_cases(
    client: TestClient, onramp_sessions: sessionmaker[Session]
) -> None:
    case_id = open_case(client, subject_id="s_2", key="t2")
    version = move_case(onramp_sessions, case_id, CaseState.ESCALATED)

    assert _propose(client, (case_id, version), user="usr_op").status_code == 403
    assert _propose(client, (case_id, version), user="usr_lead").status_code == 201


def test_other_tenant_cannot_propose(client: TestClient, case: tuple[str, int]) -> None:
    assert _propose(client, case, user="usr_g").status_code == 404


def _agent_proposal(sessions: sessionmaker[Session], case_id: str) -> str:
    with sessions() as session, session.begin():
        proposal = create_proposal(
            session,
            session.get_one(SupportCase, case_id),
            ProposalRequest(
                resolution_code=ResolutionCode.MISSING_PAYOUT,
                action_type=ActionType.PAYOUT_ADJUSTMENT,
                action_params={"amount_cents": 8400, "currency": "USD"},
                evidence_refs=EVIDENCE,
                rationale="settlement failed",
            ),
            proposer=Actor.agent(),
            occurred_at=datetime.now(UTC),
        )
        return proposal.id


def test_agent_proposal_waits_for_a_human(
    client: TestClient, case: tuple[str, int], onramp_sessions: sessionmaker[Session]
) -> None:
    proposal_id = _agent_proposal(onramp_sessions, case[0])
    assert _state(client, case[0])["state"] == "proposed"

    accepted = client.post(f"/proposals/{proposal_id}/accept", json={}, headers=as_user("usr_op"))

    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["decided_by_id"] == "usr_op"
    assert _state(client, case[0])["state"] == "awaiting_approval"
    again = client.post(f"/proposals/{proposal_id}/accept", json={}, headers=as_user("usr_op"))
    assert again.status_code == 409


def test_rejected_agent_proposal_returns_case_to_investigation(
    client: TestClient, case: tuple[str, int], onramp_sessions: sessionmaker[Session]
) -> None:
    proposal_id = _agent_proposal(onramp_sessions, case[0])

    response = client.post(
        f"/proposals/{proposal_id}/reject",
        json={"note": "payment pe_9 shows settlement succeeded"},
        headers=as_user("usr_op"),
    )

    assert response.json()["status"] == "rejected"
    assert _state(client, case[0])["state"] == "investigating"


def test_other_tenant_cannot_decide_a_proposal(
    client: TestClient, case: tuple[str, int], onramp_sessions: sessionmaker[Session]
) -> None:
    proposal_id = _agent_proposal(onramp_sessions, case[0])

    response = client.post(f"/proposals/{proposal_id}/accept", json={}, headers=as_user("usr_g"))

    assert response.status_code == 404
