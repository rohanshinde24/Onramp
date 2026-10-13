"""Write access to the marketplace. Only the Action Gateway is given a writer (ADR-008).

Every write is idempotent by ``(tenant_id, idempotency_key)``:

* the first call performs the write and stores its result in the same transaction,
* a repeat with the same key and the same request returns the stored result without acting,
* a repeat with the same key and a different request is rejected.

Concurrent calls with one key serialize on the unique index, so exactly one performs the write.
"""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from onramp.db.types import new_id
from onramp.marketplace import models as m
from onramp.marketplace.records import (
    DocumentRequestRecord,
    PaymentRecord,
    QualityReviewRecord,
    Record,
    WorkerRecord,
)

R = TypeVar("R", bound=Record)
Clock = Callable[[], datetime]


class MarketplaceError(Exception):
    """The marketplace refused the write. Nothing was changed."""


class NotFoundError(MarketplaceError):
    pass


class RejectedError(MarketplaceError):
    pass


class IdempotencyConflictError(MarketplaceError):
    """The key was already used for a different request."""


class MarketplaceWriter(Protocol):
    def issue_adjustment(
        self,
        tenant_id: str,
        worker_id: str,
        amount_cents: int,
        currency: str,
        *,
        idempotency_key: str,
    ) -> PaymentRecord: ...

    def activate_worker(
        self, tenant_id: str, worker_id: str, *, idempotency_key: str
    ) -> WorkerRecord: ...

    def overturn_review(
        self, tenant_id: str, review_id: str, *, idempotency_key: str
    ) -> QualityReviewRecord: ...

    def request_document(
        self, tenant_id: str, worker_id: str, document_kind: str, *, idempotency_key: str
    ) -> DocumentRequestRecord: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SqlMarketplaceWriter:
    def __init__(self, sessions: sessionmaker[Session], clock: Clock = _utcnow) -> None:
        self._sessions = sessions
        self._clock = clock

    def issue_adjustment(
        self,
        tenant_id: str,
        worker_id: str,
        amount_cents: int,
        currency: str,
        *,
        idempotency_key: str,
    ) -> PaymentRecord:
        request = {"worker_id": worker_id, "amount_cents": amount_cents, "currency": currency}

        def write(s: Session) -> PaymentRecord:
            if amount_cents <= 0:
                raise RejectedError("adjustment amount must be positive")
            _require_worker(s, tenant_id, worker_id)
            now = self._clock()
            payment = m.Payment(
                id=new_id("pay"),
                tenant_id=tenant_id,
                worker_id=worker_id,
                submission_id=None,
                kind="adjustment",
                amount_cents=amount_cents,
                currency=currency,
                status="settled",
                created_at=now,
            )
            s.add(payment)
            s.flush()
            for event_type in ("authorized", "settlement_succeeded"):
                s.add(
                    m.PaymentEvent(
                        id=new_id("pe"),
                        tenant_id=tenant_id,
                        payment_id=payment.id,
                        event_type=event_type,
                        amount_cents=amount_cents,
                        occurred_at=now,
                    )
                )
            return PaymentRecord(
                id=payment.id,
                tenant_id=tenant_id,
                worker_id=worker_id,
                submission_id=None,
                payment_kind="adjustment",
                amount_cents=amount_cents,
                currency=currency,
                status="settled",
                created_at=now,
            )

        return self._idempotent(
            tenant_id, idempotency_key, "issue_adjustment", request, PaymentRecord, write
        )

    def activate_worker(
        self, tenant_id: str, worker_id: str, *, idempotency_key: str
    ) -> WorkerRecord:
        def write(s: Session) -> WorkerRecord:
            worker = _require_worker(s, tenant_id, worker_id)
            if worker.status not in ("pending_onboarding", "active"):
                raise RejectedError(f"cannot activate a worker in status {worker.status}")
            worker.status = "active"
            return WorkerRecord(
                id=worker.id,
                tenant_id=tenant_id,
                full_name=worker.full_name,
                email=worker.email,
                status=worker.status,
                country=worker.country,
            )

        return self._idempotent(
            tenant_id,
            idempotency_key,
            "activate_worker",
            {"worker_id": worker_id},
            WorkerRecord,
            write,
        )

    def overturn_review(
        self, tenant_id: str, review_id: str, *, idempotency_key: str
    ) -> QualityReviewRecord:
        def write(s: Session) -> QualityReviewRecord:
            review = s.scalar(
                select(m.QualityReview)
                .filter_by(tenant_id=tenant_id, id=review_id)
                .with_for_update()
            )
            if review is None:
                raise NotFoundError(f"quality review {review_id}")
            if review.result != "fail":
                raise RejectedError("only a failed review can be overturned")
            review.result = "pass"
            review.overturned_at = self._clock()
            return QualityReviewRecord(
                id=review.id,
                tenant_id=tenant_id,
                submission_id=review.submission_id,
                reviewer_id=review.reviewer_id,
                result=review.result,
                score=review.score,
                rubric_scores=review.rubric_scores,
                reviewer_comment=review.reviewer_comment,
                reviewed_at=review.reviewed_at,
                overturned_at=review.overturned_at,
            )

        return self._idempotent(
            tenant_id,
            idempotency_key,
            "overturn_review",
            {"review_id": review_id},
            QualityReviewRecord,
            write,
        )

    def request_document(
        self, tenant_id: str, worker_id: str, document_kind: str, *, idempotency_key: str
    ) -> DocumentRequestRecord:
        request = {"worker_id": worker_id, "document_kind": document_kind}

        def write(s: Session) -> DocumentRequestRecord:
            _require_worker(s, tenant_id, worker_id)
            row = m.DocumentRequest(
                id=new_id("docreq"),
                tenant_id=tenant_id,
                worker_id=worker_id,
                document_kind=document_kind,
                requested_at=self._clock(),
            )
            s.add(row)
            return DocumentRequestRecord(
                id=row.id,
                tenant_id=tenant_id,
                worker_id=worker_id,
                document_kind=document_kind,
                requested_at=row.requested_at,
            )

        return self._idempotent(
            tenant_id,
            idempotency_key,
            "request_document",
            request,
            DocumentRequestRecord,
            write,
        )

    def _idempotent(
        self,
        tenant_id: str,
        key: str,
        operation: str,
        request: dict[str, Any],
        record_type: type[R],
        write: Callable[[Session], R],
    ) -> R:
        request_hash = hashlib.sha256(
            json.dumps({"op": operation, **request}, sort_keys=True).encode()
        ).hexdigest()
        with self._sessions() as s, s.begin():
            claimed = s.execute(
                insert(m.WriteRequest)
                .values(
                    tenant_id=tenant_id,
                    idempotency_key=key,
                    operation=operation,
                    request_hash=request_hash,
                    result={},
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "idempotency_key"])
                .returning(m.WriteRequest.id)
            ).scalar_one_or_none()

            if claimed is None:
                existing = s.scalars(
                    select(m.WriteRequest).filter_by(tenant_id=tenant_id, idempotency_key=key)
                ).one()
                if existing.request_hash != request_hash:
                    raise IdempotencyConflictError(key)
                return record_type.model_validate(existing.result)

            record = write(s)
            stored = s.get_one(m.WriteRequest, claimed)
            stored.result = record.model_dump(mode="json")
            return record


def _require_worker(s: Session, tenant_id: str, worker_id: str) -> m.Worker:
    worker = s.scalar(
        select(m.Worker).filter_by(tenant_id=tenant_id, id=worker_id).with_for_update()
    )
    if worker is None:
        raise NotFoundError(f"worker {worker_id}")
    return worker
