"""Each user-visible flow leaves exactly the expected events in the outbox, and a rejected
operation leaves none."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.cases.state import CaseState
from onramp.events.contracts import parse_payload
from onramp.events.envelope import EventEnvelope
from onramp.events.models import OutboxEvent
from tests.integration.api_support import as_user, build_client, move_case, open_case


@pytest.fixture
def client(
    onramp_sessions: sessionmaker[Session], marketplace_sessions: sessionmaker[Session]
) -> TestClient:
    return build_client(onramp_sessions, marketplace_sessions)


def _events(sessions: sessionmaker[Session], case_id: str) -> list[EventEnvelope]:
    with sessions() as session:
        rows = session.scalars(
            select(OutboxEvent).filter_by(aggregate_id=case_id).order_by(OutboxEvent.id)
        )
        return [EventEnvelope.model_validate(r.envelope) for r in rows]


def _types(sessions: sessionmaker[Session], case_id: str) -> list[str]:
    return [e.event_type for e in _events(sessions, case_id)]


def _propose(client: TestClient, case_id: str, version: int, **proposal: Any) -> Any:
    body = {
        "resolution_code": "MISSING_PAYOUT",
        "action_type": "PAYOUT_ADJUSTMENT",
        "action_params": {"amount_cents": 8400, "currency": "USD"},
        "evidence_refs": ["submission:s_1"],
        "rationale": "settlement failed",
        **proposal,
    }
    return client.post(
        f"/cases/{case_id}/proposals",
        json={"expected_version": version, "proposal": body},
        headers=as_user("usr_op"),
    )


def test_intake_emits_case_created_once(
    client: TestClient, onramp_sessions: sessionmaker[Session]
) -> None:
    case_id = open_case(client)
    open_case(client)  # repeat intake key
    open_case(client, key="ticket-2")  # attached report

    events = _events(onramp_sessions, case_id)
    assert [e.event_type for e in events] == ["case.created"]
    payload = parse_payload(events[0])
    assert payload.model_dump()["worker_id"] == "w_492"
    assert events[0].aggregate_version == 1


def test_escalation_emits_case_escalated(
    client: TestClient, onramp_sessions: sessionmaker[Session]
) -> None:
    case_id = open_case(client)
    client.post(
        f"/cases/{case_id}/escalate",
        json={"reason": "conflicting_evidence", "expected_version": 1},
        headers=as_user("usr_op"),
    )

    events = _events(onramp_sessions, case_id)
    assert [e.event_type for e in events] == ["case.created", "case.escalated"]
    assert events[1].payload == {"reason": "conflicting_evidence", "from_state": "created"}
    assert [e.aggregate_version for e in events] == [1, 2]


def test_no_action_resolution_emits_case_resolved(
    client: TestClient, onramp_sessions: sessionmaker[Session]
) -> None:
    case_id = open_case(client)
    version = move_case(onramp_sessions, case_id, CaseState.INITIAL_CHECKS, CaseState.INVESTIGATING)
    _propose(
        client, case_id, version, resolution_code="PAID_IN_FULL", action_type=None, action_params={}
    )

    events = _events(onramp_sessions, case_id)
    assert events[-1].event_type == "case.resolved"
    assert events[-1].payload == {"resolution_code": "PAID_IN_FULL"}


def test_approval_emits_approval_granted_bound_to_content(
    client: TestClient, onramp_sessions: sessionmaker[Session]
) -> None:
    case_id = open_case(client)
    version = move_case(onramp_sessions, case_id, CaseState.INITIAL_CHECKS, CaseState.INVESTIGATING)
    proposal = _propose(client, case_id, version).json()
    client.post(
        f"/proposals/{proposal['id']}/approval",
        json={"decision": "approved", "content_hash": proposal["content_hash"]},
        headers=as_user("usr_lead"),
    )

    granted = _events(onramp_sessions, case_id)[-1]
    assert granted.event_type == "approval.granted"
    assert granted.payload["content_hash"] == proposal["content_hash"]
    assert granted.payload["action_type"] == "PAYOUT_ADJUSTMENT"


def test_rejected_approval_emits_approval_rejected(
    client: TestClient, onramp_sessions: sessionmaker[Session]
) -> None:
    case_id = open_case(client)
    version = move_case(onramp_sessions, case_id, CaseState.INITIAL_CHECKS, CaseState.INVESTIGATING)
    proposal = _propose(client, case_id, version).json()
    client.post(
        f"/proposals/{proposal['id']}/approval",
        json={"decision": "rejected", "content_hash": proposal["content_hash"]},
        headers=as_user("usr_lead"),
    )

    assert _types(onramp_sessions, case_id)[-1] == "approval.rejected"


def test_rejected_operations_emit_nothing(
    client: TestClient, onramp_sessions: sessionmaker[Session]
) -> None:
    case_id = open_case(client)
    version = move_case(onramp_sessions, case_id, CaseState.INITIAL_CHECKS, CaseState.INVESTIGATING)
    before = _types(onramp_sessions, case_id)

    assert _propose(client, case_id, version, action_type=None, action_params={}).status_code == 422
    stale = client.post(
        f"/cases/{case_id}/escalate",
        json={"reason": "operator_judgment", "expected_version": 1},
        headers=as_user("usr_op"),
    )
    assert stale.status_code == 409

    assert _types(onramp_sessions, case_id) == before
