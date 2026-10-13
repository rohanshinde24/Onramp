"""Read access to the marketplace.

Every method takes the tenant from trusted application context. A record that exists in a
different tenant is indistinguishable from one that does not exist.
"""

from typing import Protocol

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session, sessionmaker

from onramp.marketplace import models as m
from onramp.marketplace.records import (
    AssignmentRecord,
    DocumentRecord,
    PaymentEventRecord,
    PaymentRecord,
    QualityReviewRecord,
    SubmissionRecord,
    TaskRecord,
    VerificationRecordView,
    WorkerRecord,
)


class MarketplaceReader(Protocol):
    def get_worker(self, tenant_id: str, worker_id: str) -> WorkerRecord | None: ...
    def get_verifications(self, tenant_id: str, worker_id: str) -> list[VerificationRecordView]: ...
    def get_documents(self, tenant_id: str, worker_id: str) -> list[DocumentRecord]: ...
    def get_task(self, tenant_id: str, task_id: str) -> TaskRecord | None: ...
    def get_assignment(self, tenant_id: str, assignment_id: str) -> AssignmentRecord | None: ...
    def get_submission(self, tenant_id: str, submission_id: str) -> SubmissionRecord | None: ...
    def get_quality_review(self, tenant_id: str, review_id: str) -> QualityReviewRecord | None: ...
    def get_quality_reviews(
        self, tenant_id: str, submission_id: str
    ) -> list[QualityReviewRecord]: ...
    def get_payment(self, tenant_id: str, payment_id: str) -> PaymentRecord | None: ...
    def get_payments_for_submission(
        self, tenant_id: str, submission_id: str
    ) -> list[PaymentRecord]: ...
    def get_payments_for_worker(self, tenant_id: str, worker_id: str) -> list[PaymentRecord]: ...
    def get_payment_events(self, tenant_id: str, payment_id: str) -> list[PaymentEventRecord]: ...


class SqlMarketplaceReader:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def get_worker(self, tenant_id: str, worker_id: str) -> WorkerRecord | None:
        with self._sessions() as s:
            row = s.scalar(select(m.Worker).filter_by(tenant_id=tenant_id, id=worker_id))
            return None if row is None else _worker(row)

    def get_verifications(self, tenant_id: str, worker_id: str) -> list[VerificationRecordView]:
        with self._sessions() as s:
            rows = s.scalars(
                select(m.VerificationRecord)
                .filter_by(tenant_id=tenant_id, worker_id=worker_id)
                .order_by(m.VerificationRecord.kind, m.VerificationRecord.id)
            )
            return [
                VerificationRecordView(
                    id=r.id,
                    tenant_id=r.tenant_id,
                    worker_id=r.worker_id,
                    verification_kind=r.kind,
                    status=r.status,
                    completed_at=r.completed_at,
                    expires_at=r.expires_at,
                )
                for r in rows
            ]

    def get_documents(self, tenant_id: str, worker_id: str) -> list[DocumentRecord]:
        with self._sessions() as s:
            rows = s.scalars(
                select(m.Document)
                .filter_by(tenant_id=tenant_id, worker_id=worker_id)
                .order_by(m.Document.uploaded_at, m.Document.id)
            )
            return [
                DocumentRecord(
                    id=r.id,
                    tenant_id=r.tenant_id,
                    worker_id=r.worker_id,
                    document_kind=r.kind,
                    content_text=r.content_text,
                    uploaded_at=r.uploaded_at,
                )
                for r in rows
            ]

    def get_task(self, tenant_id: str, task_id: str) -> TaskRecord | None:
        with self._sessions() as s:
            r = s.scalar(select(m.Task).filter_by(tenant_id=tenant_id, id=task_id))
            if r is None:
                return None
            return TaskRecord(
                id=r.id,
                tenant_id=r.tenant_id,
                title=r.title,
                task_type=r.task_type,
                payout_cents=r.payout_cents,
                currency=r.currency,
                criteria=r.criteria,
            )

    def get_assignment(self, tenant_id: str, assignment_id: str) -> AssignmentRecord | None:
        with self._sessions() as s:
            r = s.scalar(select(m.Assignment).filter_by(tenant_id=tenant_id, id=assignment_id))
            if r is None:
                return None
            return AssignmentRecord(
                id=r.id,
                tenant_id=r.tenant_id,
                task_id=r.task_id,
                worker_id=r.worker_id,
                status=r.status,
                assigned_at=r.assigned_at,
            )

    def get_submission(self, tenant_id: str, submission_id: str) -> SubmissionRecord | None:
        with self._sessions() as s:
            r = s.scalar(select(m.Submission).filter_by(tenant_id=tenant_id, id=submission_id))
            if r is None:
                return None
            return SubmissionRecord(
                id=r.id,
                tenant_id=r.tenant_id,
                assignment_id=r.assignment_id,
                status=r.status,
                submitted_at=r.submitted_at,
            )

    def get_quality_review(self, tenant_id: str, review_id: str) -> QualityReviewRecord | None:
        with self._sessions() as s:
            r = s.scalar(select(m.QualityReview).filter_by(tenant_id=tenant_id, id=review_id))
            return None if r is None else _review(r)

    def get_quality_reviews(self, tenant_id: str, submission_id: str) -> list[QualityReviewRecord]:
        with self._sessions() as s:
            rows = s.scalars(
                select(m.QualityReview)
                .filter_by(tenant_id=tenant_id, submission_id=submission_id)
                .order_by(m.QualityReview.reviewed_at, m.QualityReview.id)
            )
            return [_review(r) for r in rows]

    def get_payment(self, tenant_id: str, payment_id: str) -> PaymentRecord | None:
        found = self._payments(tenant_id, m.Payment.id == payment_id)
        return found[0] if found else None

    def get_payments_for_submission(
        self, tenant_id: str, submission_id: str
    ) -> list[PaymentRecord]:
        return self._payments(tenant_id, m.Payment.submission_id == submission_id)

    def get_payments_for_worker(self, tenant_id: str, worker_id: str) -> list[PaymentRecord]:
        return self._payments(tenant_id, m.Payment.worker_id == worker_id)

    def get_payment_events(self, tenant_id: str, payment_id: str) -> list[PaymentEventRecord]:
        with self._sessions() as s:
            rows = s.scalars(
                select(m.PaymentEvent)
                .filter_by(tenant_id=tenant_id, payment_id=payment_id)
                .order_by(m.PaymentEvent.occurred_at, m.PaymentEvent.id)
            )
            return [
                PaymentEventRecord(
                    id=r.id,
                    tenant_id=r.tenant_id,
                    payment_id=r.payment_id,
                    event_type=r.event_type,
                    amount_cents=r.amount_cents,
                    failure_reason=r.failure_reason,
                    occurred_at=r.occurred_at,
                )
                for r in rows
            ]

    def _payments(self, tenant_id: str, condition: ColumnElement[bool]) -> list[PaymentRecord]:
        with self._sessions() as s:
            rows = s.scalars(
                select(m.Payment)
                .where(m.Payment.tenant_id == tenant_id, condition)
                .order_by(m.Payment.created_at, m.Payment.id)
            )
            return [
                PaymentRecord(
                    id=r.id,
                    tenant_id=r.tenant_id,
                    worker_id=r.worker_id,
                    submission_id=r.submission_id,
                    payment_kind=r.kind,
                    amount_cents=r.amount_cents,
                    currency=r.currency,
                    status=r.status,
                    created_at=r.created_at,
                )
                for r in rows
            ]


def _worker(r: m.Worker) -> WorkerRecord:
    return WorkerRecord(
        id=r.id,
        tenant_id=r.tenant_id,
        full_name=r.full_name,
        email=r.email,
        status=r.status,
        country=r.country,
    )


def _review(r: m.QualityReview) -> QualityReviewRecord:
    return QualityReviewRecord(
        id=r.id,
        tenant_id=r.tenant_id,
        submission_id=r.submission_id,
        reviewer_id=r.reviewer_id,
        result=r.result,
        score=r.score,
        rubric_scores=r.rubric_scores,
        reviewer_comment=r.reviewer_comment,
        reviewed_at=r.reviewed_at,
        overturned_at=r.overturned_at,
    )
