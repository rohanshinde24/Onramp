"""Requesting evidence from a worker: the Action Gateway's lowest-risk operation (ADR-008).

It changes no account state, so it needs no approval, but it is still a marketplace write:
the target worker comes from the case, the document kinds are a closed set, and the request is
idempotent per case version, so a retried step cannot ask the worker twice.
"""

from dataclasses import dataclass

from onramp.cases.models import SupportCase
from onramp.cases.state import CaseState
from onramp.marketplace.records import DocumentRequestRecord
from onramp.marketplace.writer import MarketplaceWriter

REQUESTABLE = frozenset({"identity", "tax_form", "skills_assessment"})
_FROM_STATES = frozenset({CaseState.INITIAL_CHECKS, CaseState.INVESTIGATING})


class EvidenceRequestRejectedError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceRequest:
    item: str
    request: DocumentRequestRecord


class EvidenceRequester:
    def __init__(self, writer: MarketplaceWriter) -> None:
        self._writer = writer

    def request(self, case: SupportCase, items: tuple[str, ...]) -> list[EvidenceRequest]:
        if CaseState(case.state) not in _FROM_STATES:
            raise EvidenceRequestRejectedError(f"case is {case.state}")
        if not items:
            raise EvidenceRequestRejectedError("nothing to request")
        unknown = set(items) - REQUESTABLE
        if unknown:
            raise EvidenceRequestRejectedError(f"not requestable: {sorted(unknown)}")
        return [
            EvidenceRequest(
                item,
                self._writer.request_document(
                    case.tenant_id,
                    case.worker_id,
                    item,
                    idempotency_key=f"evidence:{case.id}:v{case.version}:{item}",
                ),
            )
            for item in items
        ]
