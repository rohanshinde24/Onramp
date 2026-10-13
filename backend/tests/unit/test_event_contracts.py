import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from onramp.events.contracts import (
    CONTRACTS,
    ActionExecutedV1,
    ActionFailedV1,
    CaseCreatedV1,
    InvalidEventPayloadError,
    UnsupportedEventError,
    latest_contract,
    parse_payload,
    serialize_payload,
)
from onramp.events.envelope import EventEnvelope, is_after
from onramp.events.topics import DOMAIN_TOPICS

NOW = datetime(2026, 8, 21, 12, tzinfo=UTC)


def _envelope(**overrides: Any) -> EventEnvelope:
    values: dict[str, Any] = {
        "event_id": "evt_1",
        "event_type": "case.created",
        "schema_version": 1,
        "occurred_at": NOW,
        "tenant_id": "acme",
        "aggregate_type": "case",
        "aggregate_id": "case_1",
        "aggregate_version": 1,
        "correlation_id": "corr_1",
        "payload": serialize_payload(
            CaseCreatedV1(
                case_type="payout", subject="submission:s_1", worker_id="w_1", incident_at=NOW
            )
        ),
    }
    values.update(overrides)
    return EventEnvelope.model_validate(values)


def test_envelope_round_trips_through_json() -> None:
    envelope = _envelope()

    assert EventEnvelope.from_json(envelope.to_json()) == envelope


@pytest.mark.parametrize(
    "missing",
    [
        "event_id",
        "event_type",
        "schema_version",
        "occurred_at",
        "tenant_id",
        "aggregate_type",
        "aggregate_id",
        "aggregate_version",
        "correlation_id",
        "payload",
    ],
)
def test_required_envelope_fields(missing: str) -> None:
    raw = json.loads(_envelope().to_json())
    del raw[missing]

    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(raw)


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _envelope(occurred_at=datetime(2026, 8, 21))


def test_unknown_envelope_field_is_rejected() -> None:
    raw = json.loads(_envelope().to_json())
    raw["command"] = "issue_payout"

    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(raw)


def test_partition_key_is_the_aggregate_id() -> None:
    assert _envelope().partition_key == "case_1"


def test_payload_parses_to_its_contract() -> None:
    payload = parse_payload(_envelope())

    assert isinstance(payload, CaseCreatedV1)
    assert payload.worker_id == "w_1"


def test_unsupported_version_is_reported() -> None:
    with pytest.raises(UnsupportedEventError):
        parse_payload(_envelope(schema_version=99))


def test_invalid_payload_is_reported() -> None:
    with pytest.raises(InvalidEventPayloadError):
        parse_payload(_envelope(payload={"case_type": "payout"}))


def test_wrong_aggregate_type_is_reported() -> None:
    with pytest.raises(InvalidEventPayloadError):
        parse_payload(_envelope(aggregate_type="payment"))


def test_added_optional_payload_field_stays_compatible() -> None:
    payload = dict(_envelope().payload, channel="email")

    assert isinstance(parse_payload(_envelope(payload=payload)), CaseCreatedV1)


def test_every_contract_routes_to_a_known_topic() -> None:
    assert {c.topic for c in CONTRACTS.values()} <= {t.name for t in DOMAIN_TOPICS}
    assert latest_contract("approval.granted").schema_version == 1


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        (
            "action.executed",
            ActionExecutedV1(
                action_id="act_1",
                proposal_id="prop_1",
                approval_id="appr_1",
                action_type="PAYOUT_ADJUSTMENT",
                result={"payment_id": "pay_1"},
            ),
        ),
        (
            "action.failed",
            ActionFailedV1(
                action_id="act_1",
                proposal_id="prop_1",
                approval_id="appr_1",
                action_type="PAYOUT_ADJUSTMENT",
                status="unverified",
                reason_code="postcondition_failed",
            ),
        ),
    ],
)
def test_action_payloads_parse_through_their_contract(event_type: str, payload: Any) -> None:
    parsed = parse_payload(_envelope(event_type=event_type, payload=serialize_payload(payload)))

    assert parsed == payload


def test_ordering_helper() -> None:
    assert is_after(_envelope(aggregate_version=3), None)
    assert is_after(_envelope(aggregate_version=3), 2)
    assert not is_after(_envelope(aggregate_version=3), 3)
