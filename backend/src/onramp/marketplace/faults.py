"""Fault injection around the marketplace client, for tests and evaluation scenarios.

The distinction that matters to callers:

* ``MarketplaceError`` (from the writer) means the marketplace refused; nothing changed.
* ``OutcomeUnknownError`` means the caller cannot tell whether the write happened. The only
  safe response is to retry with the same idempotency key, then verify.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeVar

from onramp.marketplace.records import (
    DocumentRequestRecord,
    PaymentRecord,
    QualityReviewRecord,
    WorkerRecord,
)
from onramp.marketplace.writer import MarketplaceWriter

T = TypeVar("T")


class OutcomeUnknownError(Exception):
    """The call timed out or the connection dropped; the write may or may not have landed."""


class Fault(StrEnum):
    TIMEOUT_BEFORE_WRITE = "timeout_before_write"
    LOST_RESPONSE = "lost_response"


@dataclass
class FaultPlan:
    """Faults keyed by (operation, 1-based call number)."""

    faults: dict[tuple[str, int], Fault] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def inject(self, operation: str, call: int, fault: Fault) -> "FaultPlan":
        self.faults[(operation, call)] = fault
        return self

    def next_fault(self, operation: str) -> Fault | None:
        self.calls[operation] += 1
        return self.faults.get((operation, self.calls[operation]))


class FaultyMarketplaceWriter:
    def __init__(self, inner: MarketplaceWriter, plan: FaultPlan) -> None:
        self._inner = inner
        self.plan = plan

    def _call(self, operation: str, perform: Callable[[], T]) -> T:
        fault = self.plan.next_fault(operation)
        if fault is Fault.TIMEOUT_BEFORE_WRITE:
            raise OutcomeUnknownError(f"{operation}: timed out before reaching marketplace")
        result = perform()
        if fault is Fault.LOST_RESPONSE:
            raise OutcomeUnknownError(f"{operation}: response lost after write")
        return result

    def issue_adjustment(
        self,
        tenant_id: str,
        worker_id: str,
        amount_cents: int,
        currency: str,
        *,
        idempotency_key: str,
    ) -> PaymentRecord:
        return self._call(
            "issue_adjustment",
            lambda: self._inner.issue_adjustment(
                tenant_id, worker_id, amount_cents, currency, idempotency_key=idempotency_key
            ),
        )

    def activate_worker(
        self, tenant_id: str, worker_id: str, *, idempotency_key: str
    ) -> WorkerRecord:
        return self._call(
            "activate_worker",
            lambda: self._inner.activate_worker(
                tenant_id, worker_id, idempotency_key=idempotency_key
            ),
        )

    def overturn_review(
        self, tenant_id: str, review_id: str, *, idempotency_key: str
    ) -> QualityReviewRecord:
        return self._call(
            "overturn_review",
            lambda: self._inner.overturn_review(
                tenant_id, review_id, idempotency_key=idempotency_key
            ),
        )

    def request_document(
        self, tenant_id: str, worker_id: str, document_kind: str, *, idempotency_key: str
    ) -> DocumentRequestRecord:
        return self._call(
            "request_document",
            lambda: self._inner.request_document(
                tenant_id, worker_id, document_kind, idempotency_key=idempotency_key
            ),
        )
