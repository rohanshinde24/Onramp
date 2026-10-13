"""Gathering a case's facts and running the check for its type."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from onramp.cases.models import CaseType, SupportCase
from onramp.checks.onboarding import OnboardingFacts, check_onboarding
from onramp.checks.outcome import CheckOutcome
from onramp.checks.payout import PaymentHistory, PayoutFacts, check_payout
from onramp.checks.policy_context import PolicyContext
from onramp.checks.quality_review import QualityReviewFacts, check_quality_review
from onramp.marketplace.reader import MarketplaceReader
from onramp.policy.applicability import applicable_version
from onramp.policy.models import PolicyPassage

# The policy that governs each case type.
POLICY_FOR: dict[CaseType, str] = {
    CaseType.PAYOUT: "payout",
    CaseType.ONBOARDING: "onboarding",
    CaseType.QUALITY_REVIEW: "quality_review",
}


class MissingRecordError(LookupError):
    """A record the case depends on no longer exists in the marketplace."""


def policy_context(session: Session, case: SupportCase) -> PolicyContext:
    """The policy version in force at the case's incident time.

    Raises NoApplicablePolicyError when none was in force.
    """
    version = applicable_version(session, POLICY_FOR[CaseType(case.case_type)], case.incident_at)
    passages = session.scalars(
        select(PolicyPassage).filter_by(policy_id=version.policy_id, version=version.version)
    )
    return PolicyContext(
        policy_id=version.policy_id,
        version=version.version,
        parameters=version.parameters,
        passages={p.section: p.passage_id for p in passages},
    )


def run_checks(
    session: Session, case: SupportCase, marketplace: MarketplaceReader, now: datetime
) -> CheckOutcome:
    policy = policy_context(session, case)
    tenant = case.tenant_id
    match CaseType(case.case_type):
        case CaseType.PAYOUT:
            submission = _need(marketplace.get_submission(tenant, case.subject_id), "submission")
            assignment = _need(
                marketplace.get_assignment(tenant, submission.assignment_id), "assignment"
            )
            task = _need(marketplace.get_task(tenant, assignment.task_id), "task")
            payments = marketplace.get_payments_for_submission(tenant, submission.id)
            return check_payout(
                PayoutFacts(
                    now=now,
                    policy=policy,
                    submission=submission,
                    assignment=assignment,
                    task=task,
                    reviews=tuple(marketplace.get_quality_reviews(tenant, submission.id)),
                    payments=tuple(
                        PaymentHistory(p, tuple(marketplace.get_payment_events(tenant, p.id)))
                        for p in payments
                    ),
                )
            )
        case CaseType.ONBOARDING:
            worker = _need(marketplace.get_worker(tenant, case.worker_id), "worker")
            return check_onboarding(
                OnboardingFacts(
                    now=now,
                    policy=policy,
                    worker=worker,
                    verifications=tuple(marketplace.get_verifications(tenant, worker.id)),
                    documents=tuple(marketplace.get_documents(tenant, worker.id)),
                )
            )
        case CaseType.QUALITY_REVIEW:
            review = _need(marketplace.get_quality_review(tenant, case.subject_id), "review")
            submission = _need(
                marketplace.get_submission(tenant, review.submission_id), "submission"
            )
            assignment = _need(
                marketplace.get_assignment(tenant, submission.assignment_id), "assignment"
            )
            task = _need(marketplace.get_task(tenant, assignment.task_id), "task")
            return check_quality_review(
                QualityReviewFacts(
                    disputed_at=case.created_at,
                    policy=policy,
                    review=review,
                    submission=submission,
                    task=task,
                )
            )


def _need[T](record: T | None, what: str) -> T:
    if record is None:
        raise MissingRecordError(what)
    return record
