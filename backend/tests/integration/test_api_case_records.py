"""Read endpoints the console uses to show a case's investigations, approvals and actions."""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from onramp.actions.models import ExecutedAction
from onramp.agent.models import InvestigationRun
from onramp.cases.state import CaseState
from tests.integration.api_support import as_user, build_client, move_case, open_case


@pytest.fixture
def client(
    onramp_sessions: sessionmaker[Session], marketplace_sessions: sessionmaker[Session]
) -> TestClient:
    return build_client(onramp_sessions, marketplace_sessions)


def _approved(client: TestClient, sessions: sessionmaker[Session]) -> dict[str, Any]:
    case_id = open_case(client)
    version = move_case(sessions, case_id, CaseState.INITIAL_CHECKS, CaseState.INVESTIGATING)
    proposal: dict[str, Any] = client.post(
        f"/cases/{case_id}/proposals",
        json={
            "expected_version": version,
            "proposal": {
                "resolution_code": "MISSING_PAYOUT",
                "action_type": "PAYOUT_ADJUSTMENT",
                "action_params": {"amount_cents": 8400, "currency": "USD"},
                "evidence_refs": ["submission:s_1"],
                "rationale": "settlement failed",
            },
        },
        headers=as_user("usr_op"),
    ).json()
    response = client.post(
        f"/proposals/{proposal['id']}/approval",
        json={"decision": "approved", "content_hash": proposal["content_hash"], "note": "ok"},
        headers=as_user("usr_lead"),
    )
    assert response.status_code == 201, response.text
    return {"case_id": case_id, "proposal": proposal, "approval": response.json()}


def test_case_shows_its_approvals_and_executed_actions(
    client: TestClient, onramp_sessions: sessionmaker[Session]
) -> None:
    approved = _approved(client, onramp_sessions)
    case_id = approved["case_id"]
    # Stands in for the gateway, which has its own tests; this checks only the read side.
    with onramp_sessions() as session, session.begin():
        session.add(
            ExecutedAction(
                id="act_1",
                tenant_id="acme",
                case_id=case_id,
                proposal_id=approved["proposal"]["id"],
                approval_id=approved["approval"]["id"],
                action_type="PAYOUT_ADJUSTMENT",
                idempotency_key="acme:act_1",
                request={"amount_cents": 8400, "currency": "USD"},
                status="succeeded",
                result={"payment_id": "pay_adj_1"},
                completed_at=datetime.now(UTC),
            )
        )

    approvals = client.get(f"/cases/{case_id}/approvals", headers=as_user("usr_op")).json()
    actions = client.get(f"/cases/{case_id}/actions", headers=as_user("usr_admin")).json()

    assert [(a["id"], a["decision"], a["approver_id"]) for a in approvals] == [
        (approved["approval"]["id"], "approved", "usr_lead")
    ]
    assert len(actions) == 1
    assert actions[0]["status"] == "succeeded"
    assert actions[0]["result"] == {"payment_id": "pay_adj_1"}
    assert actions[0]["approval_id"] == approved["approval"]["id"]


def test_case_shows_its_investigations_in_order(
    client: TestClient, onramp_sessions: sessionmaker[Session]
) -> None:
    case_id = open_case(client)
    with onramp_sessions() as session, session.begin():
        for i, status in enumerate(("model_error", "concluded"), start=1):
            session.add(
                InvestigationRun(
                    id=f"inv_{i}",
                    tenant_id="acme",
                    case_id=case_id,
                    case_version=2,
                    model_name="scripted",
                    prompt_version="investigator-v2",
                    status=status,
                    conclusion={"decision": "escalate"} if status == "concluded" else None,
                    problems=["provider timed out"] if status == "model_error" else [],
                    model_calls=i,
                    input_tokens=100 * i,
                    output_tokens=10 * i,
                    elapsed_s=0.5,
                    trajectory=[{"step": 1, "kind": "tool", "tool": "get_submission"}],
                    created_at=datetime(2026, 8, 20, 10, i, tzinfo=UTC),
                )
            )

    runs = client.get(f"/cases/{case_id}/investigations", headers=as_user("usr_op")).json()

    assert [(r["id"], r["status"]) for r in runs] == [
        ("inv_1", "model_error"),
        ("inv_2", "concluded"),
    ]
    assert runs[0]["problems"] == ["provider timed out"]
    assert runs[1]["trajectory"][0]["tool"] == "get_submission"


@pytest.mark.parametrize("path", ["investigations", "approvals", "actions"])
def test_another_tenant_cannot_read_case_records(client: TestClient, path: str) -> None:
    case_id = open_case(client)

    assert client.get(f"/cases/{case_id}/{path}", headers=as_user("usr_g")).status_code == 404
    assert client.get(f"/cases/{case_id}/{path}", headers=as_user("usr_svc")).status_code == 403
    assert client.get(f"/cases/{case_id}/{path}").status_code == 401
