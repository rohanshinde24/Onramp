from sqlalchemy.orm import Session, sessionmaker

from onramp.cases.state import CaseState
from tests.integration.api_support import as_user, build_client, move_case, open_case


class RecordingCoordinator:
    def __init__(self) -> None:
        self.starts: list[tuple[str, str]] = []
        self.signals: list[tuple[str, str, str, str]] = []

    def start_case(self, tenant_id: str, case_id: str) -> None:
        self.starts.append((tenant_id, case_id))

    def signal_case(self, tenant_id: str, case_id: str, signal: str, event_id: str) -> None:
        self.signals.append((tenant_id, case_id, signal, event_id))


def test_intake_and_approval_notify_the_workflow_directly_after_commit(
    onramp_sessions: sessionmaker[Session], marketplace_sessions: sessionmaker[Session]
) -> None:
    coordinator = RecordingCoordinator()
    client = build_client(onramp_sessions, marketplace_sessions, workflows=coordinator)
    case_id = open_case(client)
    version = move_case(onramp_sessions, case_id, CaseState.INITIAL_CHECKS, CaseState.INVESTIGATING)
    proposed = client.post(
        f"/cases/{case_id}/proposals",
        json={
            "expected_version": version,
            "proposal": {
                "resolution_code": "MISSING_PAYOUT",
                "action_type": "PAYOUT_ADJUSTMENT",
                "action_params": {"amount_cents": 8400, "currency": "USD"},
                "evidence_refs": ["submission:s_1", "payment_event:pe_1"],
                "rationale": "settlement failed",
            },
        },
        headers=as_user("usr_op"),
    )
    assert proposed.status_code == 201, proposed.text
    proposal = proposed.json()
    approved = client.post(
        f"/proposals/{proposal['id']}/approval",
        json={"decision": "approved", "content_hash": proposal["content_hash"]},
        headers=as_user("usr_lead"),
    )

    assert approved.status_code == 201, approved.text
    assert coordinator.starts == [("acme", case_id)]
    tenant_id, signaled_case_id, signal, event_id = coordinator.signals[-1]
    assert (tenant_id, signaled_case_id, signal) == ("acme", case_id, "approval_decided")
    assert event_id.startswith("evt_")
