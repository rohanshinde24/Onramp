"""What a deterministic check concludes about a case.

Checks never act. They return an outcome, and the case service decides what to do with it.
Every conclusion carries the marketplace records and policy passages it rests on.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from onramp.cases.escalation import EscalationReason
from onramp.cases.resolutions import ActionType, ResolutionCode


class OutcomeKind(StrEnum):
    RESOLVE = "resolve"  # records are conclusive; propose this resolution
    WAIT_FOR_SETTLEMENT = "wait_for_settlement"  # inside a policy window; check again later
    WAIT_FOR_EVIDENCE = "wait_for_evidence"  # a required item is missing; ask the worker
    ESCALATE = "escalate"  # policy says a person decides
    INVESTIGATE = "investigate"  # judgment needed; hand to the investigator


@dataclass(frozen=True)
class CheckOutcome:
    kind: OutcomeKind
    reason_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    citations: tuple[str, ...]
    findings: tuple[str, ...]
    resolution_code: ResolutionCode | None = None
    action_type: ActionType | None = None
    action_params: dict[str, Any] = field(default_factory=dict)
    wait_until: datetime | None = None
    missing_items: tuple[str, ...] = ()
    escalation_reason: EscalationReason | None = None


class Evidence:
    """Accumulates refs, citations and findings while a check runs."""

    def __init__(self) -> None:
        self.refs: list[str] = []
        self.citations: list[str] = []
        self.findings: list[str] = []
        self.reasons: list[str] = []

    def record(self, ref: str) -> None:
        if ref not in self.refs:
            self.refs.append(ref)

    def cite(self, passage_id: str) -> None:
        if passage_id not in self.citations:
            self.citations.append(passage_id)

    def note(self, finding: str) -> None:
        self.findings.append(finding)

    def reason(self, code: str) -> None:
        if code not in self.reasons:
            self.reasons.append(code)

    def outcome(self, kind: OutcomeKind, **kwargs: Any) -> CheckOutcome:
        return CheckOutcome(
            kind=kind,
            reason_codes=tuple(self.reasons),
            evidence_refs=tuple(self.refs),
            citations=tuple(self.citations),
            findings=tuple(self.findings),
            **kwargs,
        )
