"""Quality-review dispute checks (quality review policy, sections cited inline)."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from onramp.cases.escalation import EscalationReason
from onramp.cases.resolutions import ActionType, ResolutionCode
from onramp.checks.outcome import CheckOutcome, Evidence, OutcomeKind
from onramp.checks.policy_context import PolicyContext
from onramp.marketplace.records import QualityReviewRecord, SubmissionRecord, TaskRecord

NOT_APPLICABLE = "n/a"


@dataclass(frozen=True)
class QualityReviewFacts:
    disputed_at: datetime
    policy: PolicyContext
    review: QualityReviewRecord
    submission: SubmissionRecord
    task: TaskRecord


def check_quality_review(f: QualityReviewFacts) -> CheckOutcome:
    e = Evidence()
    for record in (f.review, f.submission, f.task):
        e.record(record.ref)

    if f.review.result == "pass":
        e.reason("REVIEW_ALREADY_PASSED")
        e.note("The review already passed; there is nothing to overturn.")
        return e.outcome(OutcomeKind.RESOLVE, resolution_code=ResolutionCode.REVIEW_UPHELD)

    window = timedelta(days=int(f.policy.value("dispute_window_days")))
    e.cite(f.policy.parameter_passage("dispute_window_days"))
    if f.disputed_at - f.review.reviewed_at > window:
        e.reason("DISPUTE_WINDOW_PASSED")
        e.note(
            f"Disputed {(f.disputed_at - f.review.reviewed_at).days} days after review; "
            f"window is {window.days} days."
        )
        return e.outcome(OutcomeKind.RESOLVE, resolution_code=ResolutionCode.OUTSIDE_DISPUTE_WINDOW)

    if f.task.criteria is None:
        # 6.1: whether a criterion applies is unclear when the instructions do not say.
        e.cite(f.policy.passage("6.1"))
        e.reason("CRITERIA_UNSPECIFIED")
        e.note("Task instructions do not list review criteria.")
        return e.outcome(OutcomeKind.ESCALATE, escalation_reason=EscalationReason.POLICY_REFERRAL)

    applicable = set(f.task.criteria)
    scored: dict[str, Decimal] = {}
    for criterion, raw in f.review.rubric_scores.items():
        if raw == NOT_APPLICABLE:
            continue
        try:
            scored[criterion] = Decimal(str(raw))
        except ArithmeticError:
            e.reason("UNREADABLE_RUBRIC")
            e.note(f"Score for {criterion!r} is not a number: {raw!r}.")
            return e.outcome(OutcomeKind.INVESTIGATE)

    unscored = sorted(applicable - scored.keys())
    if unscored:
        e.reason("APPLICABLE_CRITERION_UNSCORED")
        e.note(f"Applicable but unscored: {', '.join(unscored)}.")
        return e.outcome(OutcomeKind.INVESTIGATE)

    e.cite(f.policy.passage("2.1"))
    inapplicable = sorted(scored.keys() - applicable)
    if inapplicable:
        e.reason("INAPPLICABLE_CRITERION_SCORED")
        e.note(f"Scored criteria the instructions do not ask for: {', '.join(inapplicable)}.")

    threshold = Decimal(str(f.policy.value("pass_threshold")))
    e.cite(f.policy.parameter_passage("pass_threshold"))
    average = sum(scored[c] for c in applicable) / len(applicable)
    e.note(f"Average over applicable criteria is {average:.3f}; passing is {threshold}.")

    if average >= threshold:
        e.cite(f.policy.passage("4.1"))
        e.reason("MEETS_PASSING_SCORE")
        return e.outcome(
            OutcomeKind.RESOLVE,
            resolution_code=ResolutionCode.REVIEW_OVERTURNED,
            action_type=ActionType.OVERTURN_QUALITY_REVIEW,
        )
    e.reason("BELOW_PASSING_SCORE")
    return e.outcome(OutcomeKind.RESOLVE, resolution_code=ResolutionCode.REVIEW_UPHELD)
