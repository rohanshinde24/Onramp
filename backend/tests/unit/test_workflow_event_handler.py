from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy.orm import Session, sessionmaker

from onramp.events.envelope import EventEnvelope
from onramp.workflows.event_handler import CaseSignalHandler

NOW = datetime(2026, 10, 13, tzinfo=UTC)


class FakeCoordinator:
    def __init__(self) -> None:
        self.starts: list[tuple[str, str]] = []
        self.signals: list[tuple[str, str, str, str]] = []

    def start_case(self, tenant_id: str, case_id: str) -> None:
        self.starts.append((tenant_id, case_id))

    def signal_case(self, tenant_id: str, case_id: str, signal: str, event_id: str) -> None:
        self.signals.append((tenant_id, case_id, signal, event_id))


def _event(event_type: str, event_id: str) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        event_type=event_type,
        schema_version=1,
        occurred_at=NOW,
        tenant_id="acme",
        aggregate_type="case",
        aggregate_id="case_1",
        aggregate_version=1,
        correlation_id="corr_1",
        payload={},
    )


def test_case_created_starts_the_deterministic_workflow() -> None:
    coordinator = FakeCoordinator()
    handler = CaseSignalHandler(coordinator)

    handler.after_commit(
        _event("case.created", "evt_1"), cast(sessionmaker[Session], cast(Any, None))
    )

    assert coordinator.starts == [("acme", "case_1")]
    assert coordinator.signals == []


def test_approval_event_signals_with_the_durable_event_id() -> None:
    coordinator = FakeCoordinator()
    handler = CaseSignalHandler(coordinator)

    handler.after_commit(
        _event("approval.granted", "evt_2"), cast(sessionmaker[Session], cast(Any, None))
    )

    assert coordinator.signals == [("acme", "case_1", "approval_decided", "evt_2")]
