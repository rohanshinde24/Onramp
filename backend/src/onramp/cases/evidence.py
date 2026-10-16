"""The marketplace records a case can rest on: everything reachable from its subject.

The Action Gateway uses this to refuse evidence from outside the case, and the console uses it
to show the operator the exact records behind a case. Identity always comes from the case
record, so a namesake's or another tenant's records are never reachable.
"""

from onramp.cases.models import CaseType, SupportCase
from onramp.marketplace.reader import MarketplaceReader
from onramp.marketplace.records import Record


class EvidenceUnavailableError(Exception):
    """A record the case depends on is missing or belongs to someone else."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def case_records(reader: MarketplaceReader, case: SupportCase) -> list[Record]:
    """Every marketplace record reachable from the case, subject first, without duplicates."""
    tenant = case.tenant_id
    records: list[Record] = []
    worker = reader.get_worker(tenant, case.worker_id)
    if worker is None:
        raise EvidenceUnavailableError("worker_not_found")
    records.append(worker)

    case_type = CaseType(case.case_type)
    if case_type is CaseType.ONBOARDING:
        records.extend(reader.get_verifications(tenant, worker.id))
        records.extend(reader.get_documents(tenant, worker.id))
        return records

    if case_type is CaseType.QUALITY_REVIEW:
        review = reader.get_quality_review(tenant, case.subject_id)
        if review is None:
            raise EvidenceUnavailableError("review_not_found")
        records.append(review)
        submission_id = review.submission_id
    else:
        submission_id = case.subject_id
    submission = reader.get_submission(tenant, submission_id)
    if submission is None:
        raise EvidenceUnavailableError("submission_not_found")
    assignment = reader.get_assignment(tenant, submission.assignment_id)
    if assignment is None or assignment.worker_id != case.worker_id:
        raise EvidenceUnavailableError("subject_identity_mismatch")
    task = reader.get_task(tenant, assignment.task_id)
    if task is None:
        raise EvidenceUnavailableError("task_not_found")
    records.extend([submission, assignment, task])
    records.extend(reader.get_quality_reviews(tenant, submission.id))
    if case_type is CaseType.PAYOUT:
        for payment in reader.get_payments_for_submission(tenant, submission.id):
            records.append(payment)
            records.extend(reader.get_payment_events(tenant, payment.id))

    seen: set[str] = set()
    unique = []
    for record in records:
        if record.ref not in seen:
            seen.add(record.ref)
            unique.append(record)
    return unique
