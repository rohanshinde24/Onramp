"""Running a case's deterministic checks and applying the outcome (the fast path).

A resolution becomes a PENDING proposal from ``system``, which a person accepts or rejects
like an agent proposal: the fast path skips the model, not the human. Waiting, escalating and
handing to investigation are applied directly.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from onramp.actions.evidence import EvidenceRequester
from onramp.audit.service import Actor, record_audit
from onramp.cases.escalation import EscalationReason
from onramp.cases.models import SupportCase
from onramp.cases.queries import get_case
from onramp.cases.service import StaleCaseError, transition
from onramp.cases.state import CaseState
from onramp.checks.models import CheckRun
from onramp.checks.outcome import CheckOutcome, OutcomeKind
from onramp.checks.policy_context import PolicyGapError
from onramp.checks.runner import MissingRecordError, policy_context, run_checks
from onramp.db.types import new_id
from onramp.marketplace.reader import MarketplaceReader
from onramp.policy.applicability import NoApplicablePolicyError
from onramp.proposals.content import ProposalRequest
from onramp.proposals.service import create_proposal

log = logging.getLogger(__name__)
Clock = Callable[[], datetime]

_NEXT_STATE = {
    OutcomeKind.WAIT_FOR_SETTLEMENT: CaseState.WAITING_FOR_SETTLEMENT,
    OutcomeKind.INVESTIGATE: CaseState.INVESTIGATING,
}


class ChecksNotApplicableError(Exception):
    """Checks run only for a new case or one returned to initial checks."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CheckService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        marketplace: MarketplaceReader,
        evidence: EvidenceRequester,
        clock: Clock = _utcnow,
    ) -> None:
        self._sessions = sessions
        self._marketplace = marketplace
        self._evidence = evidence
        self._clock = clock

    def run(
        self, tenant_id: str, case_id: str, actor: Actor, *, expected_version: int | None = None
    ) -> CheckRun:
        now = self._clock()
        with self._sessions() as session, session.begin():
            case = get_case(session, tenant_id, case_id)
            if expected_version is not None and case.version != expected_version:
                raise StaleCaseError(case_id)
            if case.state == CaseState.CREATED:
                transition(
                    session,
                    case,
                    CaseState.INITIAL_CHECKS,
                    actor=actor,
                    occurred_at=now,
                    reason="checks_started",
                )
            if case.state != CaseState.INITIAL_CHECKS:
                raise ChecksNotApplicableError(f"case is {case.state}")
            run, outcome = self._evaluate(session, case, now)
            if outcome is None or outcome.kind is not OutcomeKind.WAIT_FOR_EVIDENCE:
                self._apply(session, case, run, outcome, actor, now)
                return run
            items = tuple(run.missing_items)
            case_snapshot = (case.id, case.version)

        # The evidence request is a marketplace write, outside this database's transactions.
        # If it or the step after it fails, a rerun finds the case still in INITIAL_CHECKS at
        # the same version and repeats the request under the same idempotency keys.
        requests = self._evidence.request(case, items)
        with self._sessions() as session, session.begin():
            case = get_case(session, tenant_id, case_id)
            if (case.id, case.version) != case_snapshot:
                raise StaleCaseError(case_id)
            record_audit(
                session,
                tenant_id=case.tenant_id,
                actor=actor,
                subject_type="case",
                subject_id=case.id,
                action="checks.completed",
                occurred_at=now,
                details={
                    "check_run_id": run.id,
                    "reason_codes": run.reason_codes,
                    "outcome": run.outcome,
                },
            )
            transition(
                session,
                case,
                CaseState.WAITING_FOR_EVIDENCE,
                actor=actor,
                occurred_at=now,
                reason="evidence_requested",
                details={
                    "items": list(items),
                    "requests": [r.request.ref for r in requests],
                    "check_run_id": run.id,
                },
            )
        return run

    def _evaluate(
        self, session: Session, case: SupportCase, now: datetime
    ) -> tuple[CheckRun, CheckOutcome | None]:
        """Run the checks and persist the run. The outcome is None when checks could not run."""
        run = CheckRun(
            id=new_id("chk"),
            tenant_id=case.tenant_id,
            case_id=case.id,
            case_version=case.version,
            reason_codes=[],
            evidence_refs=[],
            citations=[],
            findings=[],
            missing_items=[],
            created_at=now,
        )
        try:
            policy = policy_context(session, case)
            run.policy_id, run.policy_version = policy.policy_id, policy.version
            outcome = run_checks(session, case, self._marketplace, now)
        except (NoApplicablePolicyError, PolicyGapError, MissingRecordError) as exc:
            run.outcome = "error"
            run.error = f"{type(exc).__name__}: {exc}"
            session.add(run)
            session.flush()
            return run, None
        _fill(run, outcome)
        session.add(run)
        session.flush()
        return run, outcome

    def _apply(
        self,
        session: Session,
        case: SupportCase,
        run: CheckRun,
        outcome: CheckOutcome | None,
        actor: Actor,
        now: datetime,
    ) -> None:
        details = {"check_run_id": run.id, "reason_codes": run.reason_codes}
        record_audit(
            session,
            tenant_id=case.tenant_id,
            actor=actor,
            subject_type="case",
            subject_id=case.id,
            action="checks.completed",
            occurred_at=now,
            details={**details, "outcome": run.outcome},
        )
        if outcome is None:
            case.escalation_reason = EscalationReason.INVESTIGATION_FAILED.value
            transition(
                session,
                case,
                CaseState.ESCALATED,
                actor=actor,
                occurred_at=now,
                reason=EscalationReason.INVESTIGATION_FAILED.value,
                details={**details, "error": run.error},
            )
            return
        kind = outcome.kind
        if kind is OutcomeKind.RESOLVE:
            if outcome.resolution_code is None:
                raise ValueError("resolve outcome without a resolution code")
            proposal = create_proposal(
                session,
                case,
                ProposalRequest(
                    resolution_code=outcome.resolution_code,
                    action_type=outcome.action_type,
                    action_params=outcome.action_params,
                    reason_codes=list(outcome.reason_codes),
                    evidence_refs=[
                        *outcome.evidence_refs,
                        *(f"policy_passage:{c}" for c in outcome.citations),
                    ],
                    rationale=" ".join(outcome.findings) or "Deterministic checks.",
                ),
                proposer=Actor.system(),
                occurred_at=now,
            )
            run.proposal_id = proposal.id
        elif kind is OutcomeKind.ESCALATE:
            reason = outcome.escalation_reason or EscalationReason.OPERATOR_JUDGMENT
            case.escalation_reason = reason.value
            transition(
                session,
                case,
                CaseState.ESCALATED,
                actor=actor,
                occurred_at=now,
                reason=reason.value,
                details=details,
            )
        else:
            transition(
                session,
                case,
                _NEXT_STATE[kind],
                actor=actor,
                occurred_at=now,
                reason=kind.value,
                details={
                    **details,
                    "wait_until": run.wait_until.isoformat() if run.wait_until else None,
                },
            )


def _fill(run: CheckRun, outcome: CheckOutcome) -> None:
    run.outcome = outcome.kind.value
    run.reason_codes = list(outcome.reason_codes)
    run.evidence_refs = list(outcome.evidence_refs)
    run.citations = list(outcome.citations)
    run.findings = list(outcome.findings)
    run.missing_items = list(outcome.missing_items)
    run.wait_until = outcome.wait_until
