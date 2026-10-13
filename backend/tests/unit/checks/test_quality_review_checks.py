from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from onramp.cases.escalation import EscalationReason
from onramp.cases.resolutions import ActionType, ResolutionCode
from onramp.checks.outcome import OutcomeKind
from onramp.checks.quality_review import QualityReviewFacts, check_quality_review
from onramp.marketplace.records import QualityReviewRecord, SubmissionRecord, TaskRecord
from tests.unit.checks.support import days, policy

AUG = datetime.fromisoformat("2026-08-10T12:00:00+00:00")  # quality v5
SEPT = datetime.fromisoformat("2026-09-10T12:00:00+00:00")  # quality v6
CRITERIA = ["accuracy", "formatting"]


def facts(
    rubric: dict[str, Any],
    *,
    criteria: list[str] | None = CRITERIA,
    result: str = "fail",
    reviewed_at: datetime = SEPT,
    disputed_after: float = 2,
    version: int = 6,
) -> QualityReviewFacts:
    return QualityReviewFacts(
        disputed_at=reviewed_at + days(disputed_after),
        policy=policy("quality_review", version),
        review=QualityReviewRecord(
            id="qr_1",
            tenant_id="acme",
            submission_id="s_1",
            reviewer_id="r",
            result=result,
            score=Decimal("0.5"),
            rubric_scores=rubric,
            reviewer_comment="",
            reviewed_at=reviewed_at,
            overturned_at=None,
        ),
        submission=SubmissionRecord(
            id="s_1",
            tenant_id="acme",
            assignment_id="a_1",
            status="rejected",
            submitted_at=reviewed_at - days(1),
        ),
        task=TaskRecord(
            id="t_1",
            tenant_id="acme",
            title="t",
            task_type="transcription",
            payout_cents=6000,
            currency="USD",
            criteria=criteria,
        ),
    )


def test_scoring_an_inapplicable_criterion_can_overturn() -> None:
    # Reviewer scored timestamps, which the task never asked for.
    outcome = check_quality_review(facts({"accuracy": 0.95, "formatting": 0.80, "timestamps": 0.0}))

    assert outcome.resolution_code is ResolutionCode.REVIEW_OVERTURNED
    assert outcome.action_type is ActionType.OVERTURN_QUALITY_REVIEW
    assert "INAPPLICABLE_CRITERION_SCORED" in outcome.reason_codes
    assert {"quality_review_v6_section_2_1", "quality_review_v6_section_4_1"} <= set(
        outcome.citations
    )


def test_inapplicable_criterion_but_still_below_threshold_is_upheld() -> None:
    outcome = check_quality_review(facts({"accuracy": 0.6, "formatting": 0.6, "timestamps": 0.0}))

    assert outcome.resolution_code is ResolutionCode.REVIEW_UPHELD


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        (5, ResolutionCode.REVIEW_OVERTURNED),  # 0.72 passes the 0.70 threshold
        (6, ResolutionCode.REVIEW_UPHELD),  # but not the 0.75 one
    ],
)
def test_pass_threshold_depends_on_the_version(version: int, expected: ResolutionCode) -> None:
    reviewed = AUG if version == 5 else SEPT

    outcome = check_quality_review(
        facts({"accuracy": 0.72, "formatting": 0.72}, version=version, reviewed_at=reviewed)
    )

    assert outcome.resolution_code is expected


@pytest.mark.parametrize(
    ("version", "after", "outside"),
    [
        (5, 10, True),  # 7-day window
        (6, 10, False),  # 14-day window
        (6, 15, True),
    ],
)
def test_dispute_window_depends_on_the_version(version: int, after: float, outside: bool) -> None:
    reviewed = AUG if version == 5 else SEPT

    outcome = check_quality_review(
        facts(
            {"accuracy": 0.9, "formatting": 0.9},
            version=version,
            reviewed_at=reviewed,
            disputed_after=after,
        )
    )

    assert (outcome.resolution_code is ResolutionCode.OUTSIDE_DISPUTE_WINDOW) == outside


def test_not_applicable_marks_are_excluded() -> None:
    outcome = check_quality_review(facts({"accuracy": 0.9, "formatting": 0.8, "timestamps": "n/a"}))

    assert outcome.resolution_code is ResolutionCode.REVIEW_OVERTURNED
    assert "INAPPLICABLE_CRITERION_SCORED" not in outcome.reason_codes


def test_unspecified_criteria_are_referred_to_a_lead() -> None:
    outcome = check_quality_review(facts({"accuracy": 0.9}, criteria=None))

    assert outcome.kind is OutcomeKind.ESCALATE
    assert outcome.escalation_reason is EscalationReason.POLICY_REFERRAL
    assert "quality_review_v6_section_6_1" in outcome.citations


def test_applicable_criterion_left_unscored_needs_investigation() -> None:
    outcome = check_quality_review(facts({"accuracy": 0.9}))

    assert outcome.kind is OutcomeKind.INVESTIGATE


def test_unreadable_score_needs_investigation() -> None:
    outcome = check_quality_review(facts({"accuracy": "great", "formatting": 0.9}))

    assert outcome.kind is OutcomeKind.INVESTIGATE


def test_passed_review_has_nothing_to_overturn() -> None:
    outcome = check_quality_review(facts({"accuracy": 0.9, "formatting": 0.9}, result="pass"))

    assert outcome.resolution_code is ResolutionCode.REVIEW_UPHELD
    assert outcome.action_type is None
