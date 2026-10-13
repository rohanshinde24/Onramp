from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.audit.models import AuditEvent
from onramp.audit.service import Actor
from onramp.cases.models import CaseType, SupportCase
from onramp.cases.service import (
    CaseService,
    IntakeKeyConflictError,
    IntakeRequest,
    SubjectNotFoundError,
    WorkerMismatchError,
    transition,
)
from onramp.cases.state import CaseState, IllegalTransitionError
from onramp.identity.models import Tenant
from onramp.identity.principal import PermissionDeniedError, Principal
from onramp.identity.roles import Role
from onramp.marketplace.builder import WorldBuilder
from onramp.marketplace.reader import SqlMarketplaceReader

T0 = datetime(2026, 8, 20, 9, tzinfo=UTC)
NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)
OPERATOR = Principal("acme", "usr_op", Role.OPERATOR)


def _seed_marketplace(sessions: sessionmaker[Session]) -> None:
    with sessions() as session, session.begin():
        acme = WorldBuilder(session, "acme").tenant()
        acme.worker("w_492", "Sam Lee")
        acme.worker("w_493", "Sam Lee")  # namesake
        acme.worker("w_new", "Ana Diaz", status="pending_onboarding")
        acme.task("t_1", 8400)
        acme.assignment("a_1", "t_1", "w_492", T0)
        acme.submission("s_1", "a_1", T0 + timedelta(hours=3))
        acme.review("qr_1", "s_1", "fail", "0.40", T0 + timedelta(days=1))
        globex = WorldBuilder(session, "globex").tenant()
        globex.worker("w_g", "Lee Sam")
        globex.task("t_g", 100)
        globex.assignment("a_g", "t_g", "w_g", T0)
        globex.submission("s_g", "a_g", T0)


def _seed_onramp(sessions: sessionmaker[Session]) -> None:
    with sessions() as session, session.begin():
        session.add(Tenant(id="acme", name="Acme", payout_approval_limit_cents=50_000))


@pytest.fixture
def service(
    onramp_sessions: sessionmaker[Session], marketplace_sessions: sessionmaker[Session]
) -> CaseService:
    _seed_marketplace(marketplace_sessions)
    _seed_onramp(onramp_sessions)
    return CaseService(onramp_sessions, SqlMarketplaceReader(marketplace_sessions), lambda: NOW)


def _request(**overrides: object) -> IntakeRequest:
    values: dict[str, object] = {
        "case_type": CaseType.PAYOUT,
        "subject_id": "s_1",
        "intake_key": "ticket-1",
        "description": "I completed this task but did not receive my payout.",
    }
    values.update(overrides)
    return IntakeRequest.model_validate(values)


def _audit_actions(sessions: sessionmaker[Session], case_id: str) -> list[str]:
    with sessions() as session:
        return list(
            session.scalars(
                select(AuditEvent.action).filter_by(subject_id=case_id).order_by(AuditEvent.id)
            )
        )


def test_payout_case_takes_worker_and_incident_time_from_records(service: CaseService) -> None:
    result = service.open_case(OPERATOR, _request())

    case = result.case
    assert result.created
    assert case.state == CaseState.CREATED
    assert case.worker_id == "w_492"
    assert case.incident_at == T0 + timedelta(hours=3)
    assert case.incident_basis == "submission.submitted_at"


def test_quality_review_case_uses_review_time(service: CaseService) -> None:
    case = service.open_case(
        OPERATOR, _request(case_type=CaseType.QUALITY_REVIEW, subject_id="qr_1")
    ).case

    assert case.worker_id == "w_492"
    assert case.incident_at == T0 + timedelta(days=1)


def test_onboarding_case_uses_report_time(service: CaseService) -> None:
    case = service.open_case(
        OPERATOR, _request(case_type=CaseType.ONBOARDING, subject_id="w_new")
    ).case

    assert case.incident_at == NOW
    assert case.incident_basis == "case.reported_at"


def test_subject_from_another_tenant_is_not_found(service: CaseService) -> None:
    with pytest.raises(SubjectNotFoundError):
        service.open_case(OPERATOR, _request(subject_id="s_g"))


def test_namesake_claim_is_rejected(
    service: CaseService, onramp_sessions: sessionmaker[Session]
) -> None:
    with pytest.raises(WorkerMismatchError):
        service.open_case(OPERATOR, _request(claimed_worker_id="w_493"))

    with onramp_sessions() as session:
        assert session.scalars(select(SupportCase)).all() == []


def test_repeated_intake_key_returns_the_same_case(
    service: CaseService, onramp_sessions: sessionmaker[Session]
) -> None:
    first = service.open_case(OPERATOR, _request())
    second = service.open_case(OPERATOR, _request())

    assert not second.created
    assert second.case.id == first.case.id
    assert _audit_actions(onramp_sessions, first.case.id) == ["case.created"]


def test_intake_key_reused_for_another_subject_conflicts(service: CaseService) -> None:
    service.open_case(OPERATOR, _request())

    with pytest.raises(IntakeKeyConflictError):
        service.open_case(OPERATOR, _request(case_type=CaseType.ONBOARDING, subject_id="w_new"))


def test_second_report_attaches_to_open_case(
    service: CaseService, onramp_sessions: sessionmaker[Session]
) -> None:
    first = service.open_case(OPERATOR, _request())
    second = service.open_case(OPERATOR, _request(intake_key="ticket-2"))

    assert second.attached_to_existing
    assert second.case.id == first.case.id
    assert _audit_actions(onramp_sessions, first.case.id) == [
        "case.created",
        "case.report_attached",
    ]


def test_admin_cannot_open_cases(service: CaseService) -> None:
    with pytest.raises(PermissionDeniedError):
        service.open_case(Principal("acme", "usr_admin", Role.ADMIN), _request())


def test_transition_is_checked_and_audited(
    service: CaseService, onramp_sessions: sessionmaker[Session]
) -> None:
    case_id = service.open_case(OPERATOR, _request()).case.id

    with onramp_sessions() as session, session.begin():
        case = session.get_one(SupportCase, case_id)
        transition(
            session,
            case,
            CaseState.INITIAL_CHECKS,
            actor=Actor.system(),
            occurred_at=NOW,
            reason="intake_complete",
        )
        with pytest.raises(IllegalTransitionError):
            transition(
                session,
                case,
                CaseState.RESOLVED,
                actor=Actor.system(),
                occurred_at=NOW,
                reason="skip",
            )

    assert _audit_actions(onramp_sessions, case_id) == ["case.created", "case.transitioned"]


def test_concurrent_reports_open_exactly_one_case(
    onramp_committed: sessionmaker[Session], marketplace_committed: sessionmaker[Session]
) -> None:
    _seed_marketplace(marketplace_committed)
    _seed_onramp(onramp_committed)
    service = CaseService(
        onramp_committed, SqlMarketplaceReader(marketplace_committed), lambda: NOW
    )

    def report(i: int) -> str:
        return service.open_case(OPERATOR, _request(intake_key=f"ticket-{i}")).case.id

    with ThreadPoolExecutor(max_workers=8) as pool:
        case_ids = set(pool.map(report, range(16)))

    assert len(case_ids) == 1
    with onramp_committed() as session:
        assert len(session.scalars(select(SupportCase)).all()) == 1
