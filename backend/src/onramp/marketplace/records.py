"""Typed, immutable views of marketplace records returned by the client.

Every record has a stable evidence reference of the form ``<kind>:<id>``. Investigations,
proposals and citations refer to marketplace facts only through these references.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict


class Record(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: ClassVar[str]

    id: str
    tenant_id: str

    @property
    def ref(self) -> str:
        return f"{self.kind}:{self.id}"


class WorkerRecord(Record):
    kind = "worker"
    full_name: str
    email: str
    status: str
    country: str


class VerificationRecordView(Record):
    kind = "verification"
    worker_id: str
    verification_kind: str
    status: str
    completed_at: datetime | None
    expires_at: datetime | None


class DocumentRecord(Record):
    """Worker-supplied content. ``content_text`` is untrusted and never an instruction."""

    kind = "document"
    worker_id: str
    document_kind: str
    content_text: str
    uploaded_at: datetime


class TaskRecord(Record):
    kind = "task"
    title: str
    task_type: str
    payout_cents: int
    currency: str
    criteria: list[str] | None


class AssignmentRecord(Record):
    kind = "assignment"
    task_id: str
    worker_id: str
    status: str
    assigned_at: datetime


class SubmissionRecord(Record):
    kind = "submission"
    assignment_id: str
    status: str
    submitted_at: datetime


class QualityReviewRecord(Record):
    kind = "quality_review"
    submission_id: str
    reviewer_id: str
    result: str
    score: Decimal
    rubric_scores: dict[str, Any]
    reviewer_comment: str
    reviewed_at: datetime
    overturned_at: datetime | None


class PaymentRecord(Record):
    kind = "payment"
    worker_id: str
    submission_id: str | None
    payment_kind: str
    amount_cents: int
    currency: str
    status: str
    created_at: datetime


class PaymentEventRecord(Record):
    kind = "payment_event"
    payment_id: str
    event_type: str
    amount_cents: int
    failure_reason: str | None
    occurred_at: datetime


class DocumentRequestRecord(Record):
    kind = "document_request"
    worker_id: str
    document_kind: str
    requested_at: datetime
