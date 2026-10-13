"""Things the marketplace does on its own after a case opens: a worker uploads a document,
a delayed settlement lands or fails. Used by evaluation scenarios, tests and the demo to drive
long-running cases. Each change publishes its event in the same transaction."""

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.db.types import new_id
from onramp.events.contracts import (
    EvidenceUploadedV1,
    PaymentSettledV1,
    PaymentSettlementFailedV1,
)
from onramp.marketplace import models as m
from onramp.marketplace.events import publish
from onramp.marketplace.writer import NotFoundError, RejectedError

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MarketplaceActivity:
    def __init__(self, sessions: sessionmaker[Session], clock: Clock = _utcnow) -> None:
        self._sessions = sessions
        self._clock = clock

    def upload_document(self, tenant_id: str, worker_id: str, kind: str, content_text: str) -> str:
        now = self._clock()
        with self._sessions() as s, s.begin():
            if s.scalar(select(m.Worker).filter_by(tenant_id=tenant_id, id=worker_id)) is None:
                raise NotFoundError(f"worker {worker_id}")
            document = m.Document(
                id=new_id("doc"),
                tenant_id=tenant_id,
                worker_id=worker_id,
                kind=kind,
                content_text=content_text,
                uploaded_at=now,
            )
            s.add(document)
            s.flush()
            publish(
                s,
                "evidence.uploaded",
                tenant_id=tenant_id,
                aggregate_id=worker_id,
                payload=EvidenceUploadedV1(
                    document_id=document.id, worker_id=worker_id, document_kind=kind
                ),
                occurred_at=now,
            )
            return document.id

    def settle_payment(self, tenant_id: str, payment_id: str) -> None:
        self._settlement(tenant_id, payment_id, failure_reason=None)

    def fail_settlement(self, tenant_id: str, payment_id: str, reason: str) -> None:
        self._settlement(tenant_id, payment_id, failure_reason=reason)

    def _settlement(self, tenant_id: str, payment_id: str, failure_reason: str | None) -> None:
        now = self._clock()
        with self._sessions() as s, s.begin():
            payment = s.scalar(
                select(m.Payment).filter_by(tenant_id=tenant_id, id=payment_id).with_for_update()
            )
            if payment is None:
                raise NotFoundError(f"payment {payment_id}")
            if payment.status != "authorized":
                raise RejectedError(f"payment {payment_id} is {payment.status}")
            payment.status = "failed" if failure_reason else "settled"
            s.add(
                m.PaymentEvent(
                    id=new_id("pe"),
                    tenant_id=tenant_id,
                    payment_id=payment_id,
                    event_type="settlement_failed" if failure_reason else "settlement_succeeded",
                    amount_cents=payment.amount_cents,
                    failure_reason=failure_reason,
                    occurred_at=now,
                )
            )
            if failure_reason:
                publish(
                    s,
                    "payment.settlement_failed",
                    tenant_id=tenant_id,
                    aggregate_id=payment_id,
                    payload=PaymentSettlementFailedV1(
                        payment_id=payment_id,
                        worker_id=payment.worker_id,
                        amount_cents=payment.amount_cents,
                        failure_reason=failure_reason,
                    ),
                    occurred_at=now,
                )
            else:
                publish(
                    s,
                    "payment.settled",
                    tenant_id=tenant_id,
                    aggregate_id=payment_id,
                    payload=PaymentSettledV1(
                        payment_id=payment_id,
                        worker_id=payment.worker_id,
                        amount_cents=payment.amount_cents,
                    ),
                    occurred_at=now,
                )
