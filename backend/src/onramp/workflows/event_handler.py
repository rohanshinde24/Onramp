"""Durable Kafka fallback that wakes case workflows after committed domain events."""

from typing import ClassVar

from sqlalchemy.orm import Session, sessionmaker

from onramp.events.contracts import parse_payload
from onramp.events.envelope import EventEnvelope
from onramp.workflows.client import CaseCoordinator


class CaseSignalHandler:
    group = "onramp-case-signals"
    topics = ("onramp.cases", "onramp.actions")
    handles: ClassVar[dict[str, frozenset[int]]] = {
        "case.created": frozenset({1}),
        "case.escalated": frozenset({1}),
        "case.resolved": frozenset({1}),
        "case.closed": frozenset({1}),
        "approval.granted": frozenset({1}),
        "approval.rejected": frozenset({1}),
        "proposal.accepted": frozenset({1}),
        "proposal.rejected": frozenset({1}),
    }

    def __init__(self, coordinator: CaseCoordinator) -> None:
        self._coordinator = coordinator

    def handle(self, session: Session, envelope: EventEnvelope) -> None:
        parse_payload(envelope)

    def after_commit(self, envelope: EventEnvelope, sessions: sessionmaker[Session]) -> None:
        if envelope.event_type == "case.created":
            self._coordinator.start_case(envelope.tenant_id, envelope.aggregate_id)
            return
        signal = {
            "approval.granted": "approval_decided",
            "approval.rejected": "approval_decided",
            "case.escalated": "operator_escalated",
        }.get(envelope.event_type, "case_changed")
        self._coordinator.signal_case(
            envelope.tenant_id, envelope.aggregate_id, signal, envelope.event_id
        )
