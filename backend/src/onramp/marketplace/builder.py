"""Builds marketplace state for tests, seed data and evaluation scenarios.

It writes through the marketplace models directly because it plays the role of the
marketplace's own history, not of Onramp.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from onramp.marketplace import models as m


class WorldBuilder:
    def __init__(self, session: Session, tenant_id: str) -> None:
        self.session = session
        self.tenant_id = tenant_id

    def tenant(self, name: str | None = None) -> "WorldBuilder":
        self.session.add(m.MarketplaceTenant(id=self.tenant_id, name=name or self.tenant_id))
        self.session.flush()
        return self

    def worker(
        self,
        worker_id: str,
        full_name: str,
        *,
        status: str = "active",
        email: str | None = None,
        country: str = "US",
    ) -> str:
        email = email or f"{worker_id}@workers.example.test"
        return self._add(
            m.Worker(id=worker_id, full_name=full_name, email=email, status=status, country=country)
        )

    def verification(
        self,
        verification_id: str,
        worker_id: str,
        kind: str,
        status: str,
        *,
        completed_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> str:
        return self._add(
            m.VerificationRecord(
                id=verification_id,
                worker_id=worker_id,
                kind=kind,
                status=status,
                completed_at=completed_at,
                expires_at=expires_at,
            )
        )

    def document(
        self, document_id: str, worker_id: str, kind: str, content_text: str, uploaded_at: datetime
    ) -> str:
        return self._add(
            m.Document(
                id=document_id,
                worker_id=worker_id,
                kind=kind,
                content_text=content_text,
                uploaded_at=uploaded_at,
            )
        )

    def task(
        self, task_id: str, payout_cents: int, *, task_type: str = "labeling", currency: str = "USD"
    ) -> str:
        return self._add(
            m.Task(
                id=task_id,
                title=f"{task_type} task {task_id}",
                task_type=task_type,
                payout_cents=payout_cents,
                currency=currency,
            )
        )

    def assignment(
        self,
        assignment_id: str,
        task_id: str,
        worker_id: str,
        assigned_at: datetime,
        *,
        status: str = "submitted",
    ) -> str:
        return self._add(
            m.Assignment(
                id=assignment_id,
                task_id=task_id,
                worker_id=worker_id,
                status=status,
                assigned_at=assigned_at,
            )
        )

    def submission(
        self,
        submission_id: str,
        assignment_id: str,
        submitted_at: datetime,
        *,
        status: str = "accepted",
    ) -> str:
        return self._add(
            m.Submission(
                id=submission_id,
                assignment_id=assignment_id,
                status=status,
                submitted_at=submitted_at,
            )
        )

    def review(
        self,
        review_id: str,
        submission_id: str,
        result: str,
        score: str,
        reviewed_at: datetime,
        *,
        reviewer_id: str = "reviewer_1",
        rubric_scores: dict[str, Any] | None = None,
        comment: str = "",
    ) -> str:
        return self._add(
            m.QualityReview(
                id=review_id,
                submission_id=submission_id,
                reviewer_id=reviewer_id,
                result=result,
                score=Decimal(score),
                rubric_scores=rubric_scores or {},
                reviewer_comment=comment,
                reviewed_at=reviewed_at,
            )
        )

    def payment(
        self,
        payment_id: str,
        worker_id: str,
        amount_cents: int,
        status: str,
        created_at: datetime,
        *,
        submission_id: str | None = None,
        kind: str = "task_payout",
        currency: str = "USD",
    ) -> str:
        return self._add(
            m.Payment(
                id=payment_id,
                worker_id=worker_id,
                submission_id=submission_id,
                kind=kind,
                amount_cents=amount_cents,
                currency=currency,
                status=status,
                created_at=created_at,
            )
        )

    def payment_event(
        self,
        event_id: str,
        payment_id: str,
        event_type: str,
        amount_cents: int,
        occurred_at: datetime,
        *,
        failure_reason: str | None = None,
    ) -> str:
        return self._add(
            m.PaymentEvent(
                id=event_id,
                payment_id=payment_id,
                event_type=event_type,
                amount_cents=amount_cents,
                failure_reason=failure_reason,
                occurred_at=occurred_at,
            )
        )

    def _add(self, row: Any) -> str:
        row.tenant_id = self.tenant_id
        self.session.add(row)
        self.session.flush()
        return str(row.id)
