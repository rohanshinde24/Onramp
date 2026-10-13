"""Event payload contracts, keyed by (event_type, schema_version).

Compatibility rules:

* Adding an optional payload field is compatible and keeps the version.
* Removing or renaming a field, changing its type or meaning, or making it required is a new
  ``schema_version``. Producers emit the newest version; consumers declare which versions they
  handle and park anything else.
"""

from dataclasses import dataclass
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from onramp.events.envelope import EventEnvelope


class Payload(BaseModel):
    # Unknown fields are ignored so that adding an optional field stays compatible.
    model_config = ConfigDict(frozen=True, extra="ignore")


class CaseCreatedV1(Payload):
    case_type: str
    subject: str
    worker_id: str
    incident_at: AwareDatetime


class CaseEscalatedV1(Payload):
    reason: str
    from_state: str


class CaseResolvedV1(Payload):
    resolution_code: str


class CaseClosedV1(Payload):
    closed_by: str


class ApprovalGrantedV1(Payload):
    proposal_id: str
    approval_id: str
    action_type: str
    risk_class: str
    content_hash: str


class ApprovalRejectedV1(Payload):
    proposal_id: str
    approval_id: str


@dataclass(frozen=True)
class Contract:
    event_type: str
    schema_version: int
    topic: str
    aggregate_type: str
    payload: type[Payload]


CONTRACTS: dict[tuple[str, int], Contract] = {
    (c.event_type, c.schema_version): c
    for c in [
        Contract("case.created", 1, "onramp.cases", "case", CaseCreatedV1),
        Contract("case.escalated", 1, "onramp.cases", "case", CaseEscalatedV1),
        Contract("case.resolved", 1, "onramp.cases", "case", CaseResolvedV1),
        Contract("case.closed", 1, "onramp.cases", "case", CaseClosedV1),
        Contract("approval.granted", 1, "onramp.actions", "case", ApprovalGrantedV1),
        Contract("approval.rejected", 1, "onramp.actions", "case", ApprovalRejectedV1),
    ]
}


class UnsupportedEventError(Exception):
    """No contract for this (event_type, schema_version). Consumers park such events."""


class InvalidEventPayloadError(Exception):
    pass


def contract_for(event_type: str, schema_version: int) -> Contract:
    contract = CONTRACTS.get((event_type, schema_version))
    if contract is None:
        raise UnsupportedEventError(f"{event_type} v{schema_version}")
    return contract


def latest_contract(event_type: str) -> Contract:
    versions = [c for (t, _), c in CONTRACTS.items() if t == event_type]
    if not versions:
        raise UnsupportedEventError(event_type)
    return max(versions, key=lambda c: c.schema_version)


def parse_payload(envelope: EventEnvelope) -> Payload:
    contract = contract_for(envelope.event_type, envelope.schema_version)
    if envelope.aggregate_type != contract.aggregate_type:
        raise InvalidEventPayloadError(
            f"{envelope.event_type} must have aggregate_type {contract.aggregate_type}"
        )
    try:
        return contract.payload.model_validate(envelope.payload)
    except ValidationError as exc:
        raise InvalidEventPayloadError(
            f"{envelope.event_type}: {exc.error_count()} errors"
        ) from exc


def serialize_payload(payload: Payload) -> dict[str, Any]:
    return payload.model_dump(mode="json")
