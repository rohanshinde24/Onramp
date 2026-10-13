from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from onramp.cases.escalation import EscalationReason
from onramp.cases.resolutions import ActionType, ResolutionCode
from onramp.checks.outcome import OutcomeKind
from onramp.checks.payout import PaymentHistory, PayoutFacts, check_payout
from onramp.marketplace.records import (
    AssignmentRecord,
    PaymentEventRecord,
    PaymentRecord,
    QualityReviewRecord,
    SubmissionRecord,
    TaskRecord,
)
from tests.unit.checks.support import T0, days, policy

TENANT = "acme"
LATER = T0 + days(30)  # long after every settlement window


def _review(result: str = "pass", overturned: bool = False) -> QualityReviewRecord:
    return QualityReviewRecord(
        id="qr_1",
        tenant_id=TENANT,
        submission_id="s_1",
        reviewer_id="r",
        result=result,
        score=Decimal("0.9"),
        rubric_scores={},
        reviewer_comment="",
        reviewed_at=T0 + days(0.2),
        overturned_at=T0 + days(1) if overturned else None,
    )


def _payment(
    *events: tuple[str, int, float], currency: str = "USD", pid: str = "pay_1"
) -> PaymentHistory:
    payment = PaymentRecord(
        id=pid,
        tenant_id=TENANT,
        worker_id="w_1",
        submission_id="s_1",
        payment_kind="task_payout",
        amount_cents=8400,
        currency=currency,
        status="authorized",
        created_at=T0 + days(0.5),
    )
    return PaymentHistory(
        payment,
        tuple(
            PaymentEventRecord(
                id=f"{pid}_e{i}",
                tenant_id=TENANT,
                payment_id=pid,
                event_type=t,
                amount_cents=amount,
                failure_reason=None,
                occurred_at=T0 + days(at),
            )
            for i, (t, amount, at) in enumerate(events)
        ),
    )


def facts(
    *,
    now: datetime = LATER,
    version: int = 12,
    status: str = "accepted",
    reviews: tuple[QualityReviewRecord, ...] | None = None,
    payments: tuple[PaymentHistory, ...] = (),
) -> PayoutFacts:
    return PayoutFacts(
        now=now,
        policy=policy("payout", version),
        submission=SubmissionRecord(
            id="s_1", tenant_id=TENANT, assignment_id="a_1", status=status, submitted_at=T0
        ),
        assignment=AssignmentRecord(
            id="a_1",
            tenant_id=TENANT,
            task_id="t_1",
            worker_id="w_1",
            status="submitted",
            assigned_at=T0 - days(1),
        ),
        task=TaskRecord(
            id="t_1",
            tenant_id=TENANT,
            title="t",
            task_type="labeling",
            payout_cents=8400,
            currency="USD",
            criteria=None,
        ),
        reviews=reviews if reviews is not None else (_review(),),
        payments=payments,
    )


AUTHORIZED = ("authorized", 8400, 0.5)


def test_failed_settlement_after_window_is_missing_payout() -> None:
    outcome = check_payout(facts(payments=(_payment(AUTHORIZED, ("settlement_failed", 8400, 3)),)))

    assert outcome.kind is OutcomeKind.RESOLVE
    assert outcome.resolution_code is ResolutionCode.MISSING_PAYOUT
    assert outcome.action_type is ActionType.PAYOUT_ADJUSTMENT
    assert outcome.action_params == {"amount_cents": 8400, "currency": "USD"}
    assert {"payout_v12_section_4_3", "payout_v12_section_5_1", "payout_v12_section_4_2"} <= set(
        outcome.citations
    )
    assert {"payment:pay_1", "payment_event:pay_1_e1", "submission:s_1"} <= set(
        outcome.evidence_refs
    )


@pytest.mark.parametrize(("version", "waits"), [(11, False), (12, True)])
def test_settlement_window_depends_on_the_version_in_force(version: int, waits: bool) -> None:
    # Six days after authorization: past v11's 5-day window, inside v12's 7-day window.
    now = T0 + days(0.5 + 6)

    outcome = check_payout(facts(now=now, version=version, payments=(_payment(AUTHORIZED),)))

    if waits:
        assert outcome.kind is OutcomeKind.WAIT_FOR_SETTLEMENT
        assert outcome.wait_until == T0 + days(0.5 + 7)
    else:
        assert outcome.resolution_code is ResolutionCode.MISSING_PAYOUT
        assert "payout_v11_section_4_3" in outcome.citations


def test_settled_in_full_needs_no_action() -> None:
    outcome = check_payout(
        facts(payments=(_payment(AUTHORIZED, ("settlement_succeeded", 8400, 2)),))
    )

    assert outcome.resolution_code is ResolutionCode.PAID_IN_FULL
    assert outcome.action_type is None


def test_partial_settlement_under_v12_is_underpaid_by_the_difference() -> None:
    outcome = check_payout(
        facts(payments=(_payment(AUTHORIZED, ("settlement_succeeded", 6000, 2)),))
    )

    assert outcome.resolution_code is ResolutionCode.UNDERPAID
    assert outcome.action_params == {"amount_cents": 2400, "currency": "USD"}
    assert "payout_v12_section_4_4" in outcome.citations


def test_partial_settlement_under_v11_is_not_guessed() -> None:
    outcome = check_payout(
        facts(
            version=11,
            now=T0 + days(30),
            payments=(_payment(AUTHORIZED, ("settlement_succeeded", 6000, 2)),),
        )
    )

    assert outcome.kind is OutcomeKind.INVESTIGATE
    assert "PARTIAL_SETTLEMENT_NOT_COVERED" in outcome.reason_codes


def test_duplicate_payments_escalate() -> None:
    outcome = check_payout(
        facts(payments=(_payment(AUTHORIZED), _payment(AUTHORIZED, pid="pay_2")))
    )

    assert outcome.kind is OutcomeKind.ESCALATE
    assert outcome.escalation_reason is EscalationReason.POLICY_REFERRAL
    assert "payout_v12_section_6_1" in outcome.citations


def test_rejected_submission_is_not_eligible() -> None:
    outcome = check_payout(facts(status="rejected", reviews=(_review("fail"),)))

    assert outcome.resolution_code is ResolutionCode.NOT_ELIGIBLE
    assert outcome.citations == ("payout_v12_section_2_1",)


def test_overturned_review_makes_a_rejected_submission_eligible() -> None:
    outcome = check_payout(facts(status="rejected", reviews=(_review("pass", overturned=True),)))

    assert outcome.resolution_code is ResolutionCode.MISSING_PAYOUT


def test_never_authorized_waits_for_authorization_plus_window() -> None:
    outcome = check_payout(facts(now=T0 + days(3)))

    assert outcome.kind is OutcomeKind.WAIT_FOR_SETTLEMENT
    assert outcome.wait_until == T0 + days(1 + 7)


def test_currency_mismatch_goes_to_investigation() -> None:
    outcome = check_payout(facts(payments=(_payment(AUTHORIZED, currency="EUR"),)))

    assert outcome.kind is OutcomeKind.INVESTIGATE


def test_overpayment_goes_to_investigation() -> None:
    outcome = check_payout(
        facts(payments=(_payment(AUTHORIZED, ("settlement_succeeded", 9000, 2)),))
    )

    assert outcome.kind is OutcomeKind.INVESTIGATE


def test_every_citation_belongs_to_the_version_in_force() -> None:
    for version in (11, 12):
        f = replace(facts(version=version), payments=(_payment(AUTHORIZED),))
        outcome = check_payout(f)
        assert all(c.startswith(f"payout_v{version}_") for c in outcome.citations)
