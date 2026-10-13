from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from onramp.actions.evidence import EvidenceRequester
from onramp.actions.gateway import ActionGateway
from onramp.audit.service import Actor
from onramp.cases.models import SupportCase
from onramp.cases.service import transition
from onramp.cases.state import CaseState
from onramp.checks.models import CheckRun
from onramp.checks.service import CheckService
from onramp.db.types import new_id
from onramp.entrypoints.seed import seed
from onramp.marketplace.reader import SqlMarketplaceReader
from onramp.marketplace.writer import SqlMarketplaceWriter
from onramp.policy.corpus import load_corpus
from onramp.policy.loader import load_documents
from onramp.workflows.activities import WorkflowActivities
from onramp.workflows.contracts import CaseWorkflowInput

NOW = datetime(2026, 10, 1, tzinfo=UTC)
CORPUS = Path(__file__).resolve().parents[3] / "policies"


def _activities(
    onramp: sessionmaker[Session], marketplace: sessionmaker[Session]
) -> WorkflowActivities:
    seed(onramp, marketplace, NOW)
    with onramp() as session, session.begin():
        load_documents(session, load_corpus(CORPUS))
    reader = SqlMarketplaceReader(marketplace)
    writer = SqlMarketplaceWriter(marketplace, lambda: NOW)
    checks = CheckService(onramp, reader, EvidenceRequester(writer), lambda: NOW)
    return WorkflowActivities(
        onramp,
        checks,
        ActionGateway(onramp, reader, writer, lambda: NOW),
        lambda: NOW,
    )


def _case_id(onramp: sessionmaker[Session], subject_id: str) -> str:
    with onramp() as session:
        return session.scalars(select(SupportCase.id).filter_by(subject_id=subject_id)).one()


def test_check_activity_is_idempotent_after_its_commit(
    onramp_sessions: sessionmaker[Session], marketplace_sessions: sessionmaker[Session]
) -> None:
    activities = _activities(onramp_sessions, marketplace_sessions)
    case_id = _case_id(onramp_sessions, "acme_s_8821")
    request = CaseWorkflowInput("acme", case_id)

    first = activities.run_case_checks(request)
    replay = activities.run_case_checks(request)

    assert first.state == CaseState.PROPOSED
    assert replay.state == CaseState.PROPOSED
    with onramp_sessions() as session:
        assert session.scalar(select(func.count()).select_from(CheckRun)) == 1


def test_settlement_resume_rechecks_current_state_and_deadline(
    onramp_sessions: sessionmaker[Session], marketplace_sessions: sessionmaker[Session]
) -> None:
    activities = _activities(onramp_sessions, marketplace_sessions)
    case_id = _case_id(onramp_sessions, "acme_s_8821")
    deadline = NOW + timedelta(days=3)
    with onramp_sessions() as session, session.begin():
        case = session.get_one(SupportCase, case_id)
        transition(
            session,
            case,
            CaseState.INITIAL_CHECKS,
            actor=Actor.system(),
            occurred_at=NOW,
            reason="test_setup",
        )
        session.add(
            CheckRun(
                id=new_id("chk"),
                tenant_id=case.tenant_id,
                case_id=case.id,
                case_version=case.version,
                outcome="wait_for_settlement",
                reason_codes=[],
                evidence_refs=[],
                citations=[],
                findings=[],
                missing_items=[],
                wait_until=deadline,
                created_at=NOW,
            )
        )
        transition(
            session,
            case,
            CaseState.WAITING_FOR_SETTLEMENT,
            actor=Actor.system(),
            occurred_at=NOW,
            reason="test_setup",
        )
    request = CaseWorkflowInput("acme", case_id)

    waiting = activities.read_case(request)
    first = activities.resume_settlement_checks(request)
    replay = activities.resume_settlement_checks(request)

    assert waiting.wait_until == deadline
    assert first.state == CaseState.INITIAL_CHECKS
    assert replay.state == CaseState.INITIAL_CHECKS
