"""Turns marketplace events into case progress (ARCHITECTURE: request and case flow, step 7).

Events are notifications: the handler re-reads marketplace state before acting, so delivery
order does not matter. For each event it finds the open case the event concerns, records the
evidence on the case timeline, and resumes the case if it was waiting for exactly this.

Phase 12 adds a Temporal signal alongside the state change; the deduplication and
re-read rules stay the same.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from onramp.audit.service import Actor, record_audit
from onramp.cases.models import CaseType, SupportCase
from onramp.cases.service import transition
from onramp.cases.state import TERMINAL_STATES, CaseState
from onramp.events.contracts import (
    EvidenceUploadedV1,
    PaymentSettledV1,
    PaymentSettlementFailedV1,
    parse_payload,
)
from onramp.events.envelope import EventEnvelope
from onramp.marketplace.reader import MarketplaceReader

Clock = Callable[[], datetime]

# Which waiting state a kind of evidence can end.
_RESUMES = {
    CaseState.WAITING_FOR_EVIDENCE: CaseState.INVESTIGATING,
    CaseState.WAITING_FOR_SETTLEMENT: CaseState.INITIAL_CHECKS,
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CaseEvidenceHandler:
    group = "onramp-case-evidence"

    def __init__(self, marketplace: MarketplaceReader, clock: Clock = _utcnow) -> None:
        self._marketplace = marketplace
        self._clock = clock
        self.topics = ("marketplace.workers", "marketplace.payments")
        self.handles = {
            "evidence.uploaded": frozenset({1}),
            "payment.settled": frozenset({1}),
            "payment.settlement_failed": frozenset({1}),
        }

    def handle(self, session: Session, envelope: EventEnvelope) -> None:
        payload = parse_payload(envelope)
        if isinstance(payload, EvidenceUploadedV1):
            case = _open_case(session, envelope.tenant_id, CaseType.ONBOARDING, payload.worker_id)
            self._record(
                session,
                case,
                envelope,
                f"document:{payload.document_id}",
                waiting=CaseState.WAITING_FOR_EVIDENCE,
            )
        elif isinstance(payload, PaymentSettledV1 | PaymentSettlementFailedV1):
            payment = self._marketplace.get_payment(envelope.tenant_id, payload.payment_id)
            submission_id = payment.submission_id if payment else None
            if submission_id is None:
                return  # an adjustment, or a payment no payout case can be about
            case = _open_case(session, envelope.tenant_id, CaseType.PAYOUT, submission_id)
            self._record(
                session,
                case,
                envelope,
                f"payment:{payload.payment_id}",
                waiting=CaseState.WAITING_FOR_SETTLEMENT,
            )

    def _record(
        self,
        session: Session,
        case: SupportCase | None,
        envelope: EventEnvelope,
        evidence_ref: str,
        *,
        waiting: CaseState,
    ) -> None:
        if case is None:
            return
        now = self._clock()
        record_audit(
            session,
            tenant_id=case.tenant_id,
            actor=Actor.system(),
            subject_type="case",
            subject_id=case.id,
            action="case.evidence_arrived",
            occurred_at=now,
            details={"evidence_ref": evidence_ref, "event_type": envelope.event_type},
            correlation_id=envelope.correlation_id,
            causation_id=envelope.event_id,
        )
        if CaseState(case.state) is waiting:
            transition(
                session,
                case,
                _RESUMES[waiting],
                actor=Actor.system(),
                occurred_at=now,
                reason="evidence_arrived",
                details={"evidence_ref": evidence_ref},
                correlation_id=envelope.correlation_id,
                causation_id=envelope.event_id,
            )


def _open_case(
    session: Session, tenant_id: str, case_type: CaseType, subject_id: str
) -> SupportCase | None:
    return session.scalar(
        select(SupportCase)
        .where(
            SupportCase.tenant_id == tenant_id,
            SupportCase.case_type == case_type.value,
            SupportCase.subject_id == subject_id,
            SupportCase.state.not_in([s.value for s in TERMINAL_STATES]),
        )
        .with_for_update()
    )
