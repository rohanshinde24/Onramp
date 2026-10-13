"""Marketplace activity resumes waiting cases, end to end from the marketplace outbox through
the consumer runner (Kafka replaced by passing the outbox rows directly)."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.audit.models import AuditEvent
from onramp.cases.evidence_consumer import CaseEvidenceHandler
from onramp.cases.models import CaseType, SupportCase
from onramp.cases.service import CaseService, IntakeRequest
from onramp.cases.state import CaseState
from onramp.events.consumer import ConsumerRunner, Outcome, RawMessage
from onramp.identity.models import Tenant
from onramp.identity.principal import Principal
from onramp.identity.roles import Role
from onramp.marketplace.activity import MarketplaceActivity
from onramp.marketplace.builder import WorldBuilder
from onramp.marketplace.models import MarketplaceOutboxEvent
from onramp.marketplace.reader import SqlMarketplaceReader
from tests.integration.api_support import move_case
from tests.integration.test_consumer_runner import FakeDeadLetters, FakeSource

T0 = datetime(2026, 8, 20, 9, tzinfo=UTC)
SERVICE = Principal("acme", "usr_svc", Role.MARKETPLACE_SERVICE)
World = tuple[sessionmaker[Session], sessionmaker[Session]]


@pytest.fixture
def world(
    onramp_sessions: sessionmaker[Session], marketplace_sessions: sessionmaker[Session]
) -> World:
    with marketplace_sessions() as session, session.begin():
        w = WorldBuilder(session, "acme").tenant()
        w.worker("w_new", "Ana Diaz", status="pending_onboarding")
        w.worker("w_1", "Sam Lee")
        w.task("t_1", 8400)
        w.assignment("a_1", "t_1", "w_1", T0)
        w.submission("s_1", "a_1", T0)
        w.payment("pay_1", "w_1", 8400, "authorized", T0, submission_id="s_1")
        WorldBuilder(session, "globex").tenant().worker("w_g", "Ana Diaz")
    with onramp_sessions() as session, session.begin():
        session.add(Tenant(id="acme", name="Acme", payout_approval_limit_cents=50_000))
    return onramp_sessions, marketplace_sessions


def _open(world: World, case_type: CaseType, subject: str) -> str:
    onramp, marketplace = world
    service = CaseService(onramp, SqlMarketplaceReader(marketplace), lambda: T0)
    request = IntakeRequest(
        case_type=case_type, subject_id=subject, intake_key=subject, description="help"
    )
    return service.open_case(SERVICE, request).case.id


def _deliver(world: World) -> list[Outcome]:
    """Feed every marketplace outbox row through the consumer, as Kafka would."""
    onramp, marketplace = world
    runner = ConsumerRunner(
        onramp,
        FakeSource(),
        FakeDeadLetters(),
        CaseEvidenceHandler(SqlMarketplaceReader(marketplace), clock=lambda: T0),
        sleep=lambda _: None,
    )
    with marketplace() as session:
        rows = session.scalars(select(MarketplaceOutboxEvent).order_by(MarketplaceOutboxEvent.id))
        messages = [
            RawMessage(
                r.topic,
                0,
                r.id,
                r.partition_key.encode(),
                json.dumps(r.envelope).encode(),
            )
            for r in rows
        ]
    return [runner.process(m) for m in messages]


def _case(onramp: sessionmaker[Session], case_id: str) -> SupportCase:
    with onramp() as session:
        return session.get_one(SupportCase, case_id)


def _timeline(onramp: sessionmaker[Session], case_id: str) -> list[AuditEvent]:
    with onramp() as session:
        return list(
            session.scalars(
                select(AuditEvent).filter_by(subject_id=case_id).order_by(AuditEvent.id)
            )
        )


def test_uploaded_document_resumes_case_waiting_for_evidence(world: World) -> None:
    onramp, marketplace = world
    case_id = _open(world, CaseType.ONBOARDING, "w_new")
    move_case(onramp, case_id, CaseState.INITIAL_CHECKS, CaseState.WAITING_FOR_EVIDENCE)
    MarketplaceActivity(marketplace, lambda: T0 + timedelta(hours=6)).upload_document(
        "acme", "w_new", "tax_form", "W-9"
    )

    assert _deliver(world) == [Outcome.APPLIED]

    assert _case(onramp, case_id).state == CaseState.INVESTIGATING
    arrived = [e for e in _timeline(onramp, case_id) if e.action == "case.evidence_arrived"]
    assert len(arrived) == 1
    assert arrived[0].details["evidence_ref"].startswith("document:")
    assert arrived[0].causation_id is not None


def test_redelivered_evidence_event_resumes_only_once(world: World) -> None:
    onramp, marketplace = world
    case_id = _open(world, CaseType.ONBOARDING, "w_new")
    move_case(onramp, case_id, CaseState.INITIAL_CHECKS, CaseState.WAITING_FOR_EVIDENCE)
    MarketplaceActivity(marketplace, lambda: T0).upload_document("acme", "w_new", "tax_form", "x")

    assert _deliver(world) == [Outcome.APPLIED]
    assert _deliver(world) == [Outcome.DUPLICATE]
    transitions = [e for e in _timeline(onramp, case_id) if e.action == "case.transitioned"]
    assert [t.details["to"] for t in transitions] == [
        "initial_checks",
        "waiting_for_evidence",
        "investigating",
    ]


def test_evidence_for_a_case_not_waiting_is_recorded_without_transition(world: World) -> None:
    onramp, marketplace = world
    case_id = _open(world, CaseType.ONBOARDING, "w_new")
    MarketplaceActivity(marketplace, lambda: T0).upload_document("acme", "w_new", "id", "x")

    _deliver(world)

    assert _case(onramp, case_id).state == CaseState.CREATED
    assert [e.action for e in _timeline(onramp, case_id)] == [
        "case.created",
        "case.evidence_arrived",
    ]


def test_settlement_failure_rechecks_payout_waiting_for_settlement(world: World) -> None:
    onramp, marketplace = world
    case_id = _open(world, CaseType.PAYOUT, "s_1")
    move_case(onramp, case_id, CaseState.INITIAL_CHECKS, CaseState.WAITING_FOR_SETTLEMENT)
    MarketplaceActivity(marketplace, lambda: T0).fail_settlement("acme", "pay_1", "closed")

    _deliver(world)

    assert _case(onramp, case_id).state == CaseState.INITIAL_CHECKS


def test_event_in_another_tenant_does_not_touch_this_tenants_case(world: World) -> None:
    onramp, marketplace = world
    case_id = _open(world, CaseType.ONBOARDING, "w_new")
    move_case(onramp, case_id, CaseState.INITIAL_CHECKS, CaseState.WAITING_FOR_EVIDENCE)
    MarketplaceActivity(marketplace, lambda: T0).upload_document("globex", "w_g", "tax_form", "x")

    _deliver(world)

    assert _case(onramp, case_id).state == CaseState.WAITING_FOR_EVIDENCE
