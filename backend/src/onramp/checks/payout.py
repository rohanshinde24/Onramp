"""Payout dispute checks (payout policy, sections cited inline)."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from onramp.cases.escalation import EscalationReason
from onramp.cases.resolutions import ActionType, ResolutionCode
from onramp.checks.outcome import CheckOutcome, Evidence, OutcomeKind
from onramp.checks.policy_context import PolicyContext
from onramp.marketplace.records import (
    AssignmentRecord,
    PaymentEventRecord,
    PaymentRecord,
    QualityReviewRecord,
    SubmissionRecord,
    TaskRecord,
)

AUTHORIZATION_DAYS = 1  # section 4.1


@dataclass(frozen=True)
class PaymentHistory:
    payment: PaymentRecord
    events: tuple[PaymentEventRecord, ...]


@dataclass(frozen=True)
class PayoutFacts:
    now: datetime
    policy: PolicyContext
    submission: SubmissionRecord
    assignment: AssignmentRecord
    task: TaskRecord
    reviews: tuple[QualityReviewRecord, ...]
    payments: tuple[PaymentHistory, ...]


def check_payout(f: PayoutFacts) -> CheckOutcome:
    e = Evidence()
    for record in (f.submission, f.task):
        e.record(record.ref)
    review = max(f.reviews, key=lambda r: r.reviewed_at) if f.reviews else None
    if review is not None:
        e.record(review.ref)

    # 2.1 Eligibility: accepted and passed review; an overturned review counts as passed.
    passed = review is not None and review.result == "pass"
    overturned = review is not None and review.overturned_at is not None
    accepted = f.submission.status == "accepted" or (passed and overturned)
    if not (passed and accepted):
        e.cite(f.policy.passage("2.1"))
        e.reason("SUBMISSION_NOT_ELIGIBLE")
        e.note(
            f"Submission is {f.submission.status}; latest review is "
            f"{review.result if review else 'missing'}."
        )
        return e.outcome(OutcomeKind.RESOLVE, resolution_code=ResolutionCode.NOT_ELIGIBLE)

    # 3.1 Amount owed is the task payout.
    owed = f.task.payout_cents
    e.cite(f.policy.passage("3.1"))
    e.note(f"Amount owed is {owed} cents ({f.task.currency}).")

    task_payouts = [h for h in f.payments if h.payment.payment_kind == "task_payout"]
    for history in task_payouts:
        e.record(history.payment.ref)
        for event in history.events:
            e.record(event.ref)

    # 6.1 More than one payment for one submission: a person decides.
    if len(task_payouts) > 1:
        e.cite(f.policy.passage("6.1"))
        e.reason("DUPLICATE_PAYMENTS")
        e.note(f"{len(task_payouts)} payments exist for this submission.")
        return e.outcome(OutcomeKind.ESCALATE, escalation_reason=EscalationReason.POLICY_REFERRAL)

    window = timedelta(days=int(f.policy.value("settlement_window_days")))
    e.cite(f.policy.parameter_passage("settlement_window_days"))

    if not task_payouts:
        # 4.1 authorization should follow acceptance within a day; then the window runs.
        due = f.submission.submitted_at + timedelta(days=AUTHORIZATION_DAYS) + window
        e.cite(f.policy.passage("4.1"))
        e.note("No payment was ever authorized for this submission.")
        return _missing_or_wait(e, f, owed, due, failed=False)

    (history,) = task_payouts
    if history.payment.currency != f.task.currency:
        e.reason("CURRENCY_MISMATCH")
        e.note(f"Payment is in {history.payment.currency}, task in {f.task.currency}.")
        return e.outcome(OutcomeKind.INVESTIGATE)

    settled = sum(
        ev.amount_cents for ev in history.events if ev.event_type == "settlement_succeeded"
    )
    failed = any(ev.event_type == "settlement_failed" for ev in history.events)
    authorized = [ev.occurred_at for ev in history.events if ev.event_type == "authorized"]
    authorized_at = min(authorized) if authorized else history.payment.created_at

    if settled == owed:
        e.reason("SETTLED_IN_FULL")
        e.note(f"Settlement of {settled} cents succeeded.")
        return e.outcome(OutcomeKind.RESOLVE, resolution_code=ResolutionCode.PAID_IN_FULL)
    if settled > owed:
        e.reason("OVERPAID")
        e.note(f"Settled {settled} cents against {owed} owed.")
        return e.outcome(OutcomeKind.INVESTIGATE)
    if settled > 0:
        # 4.4 exists only from version 12. Earlier versions say nothing about partial
        # settlement, so the check does not invent an answer.
        if not f.policy.has_section("4.4"):
            e.reason("PARTIAL_SETTLEMENT_NOT_COVERED")
            e.note(f"Settled {settled} of {owed} cents; this policy version has no rule for it.")
            return e.outcome(OutcomeKind.INVESTIGATE)
        e.cite(f.policy.passage("4.4"))
        e.reason("PARTIAL_SETTLEMENT")
        e.note(f"Settled {settled} of {owed} cents.")
        return e.outcome(
            OutcomeKind.RESOLVE,
            resolution_code=ResolutionCode.UNDERPAID,
            action_type=ActionType.PAYOUT_ADJUSTMENT,
            action_params={"amount_cents": owed - settled, "currency": f.task.currency},
        )

    return _missing_or_wait(e, f, owed, authorized_at + window, failed=failed)


def _missing_or_wait(
    e: Evidence, f: PayoutFacts, owed: int, due: datetime, *, failed: bool
) -> CheckOutcome:
    if failed:
        e.cite(f.policy.passage("5.1"))
        e.reason("SETTLEMENT_FAILED")
    if f.now < due:
        e.reason("WITHIN_SETTLEMENT_WINDOW")
        e.note(f"Settlement window ends {due.isoformat()}.")
        return e.outcome(OutcomeKind.WAIT_FOR_SETTLEMENT, wait_until=due)
    e.cite(f.policy.passage("4.3"))
    e.reason("MISSING_SETTLEMENT")
    e.note(f"No successful settlement by {due.isoformat()}.")
    return e.outcome(
        OutcomeKind.RESOLVE,
        resolution_code=ResolutionCode.MISSING_PAYOUT,
        action_type=ActionType.PAYOUT_ADJUSTMENT,
        action_params={"amount_cents": owed, "currency": f.task.currency},
    )
