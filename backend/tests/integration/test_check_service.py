"""The fast path end to end: checks run on demo cases and their outcomes are applied."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from onramp.actions.evidence import EvidenceRequester
from onramp.audit.service import Actor
from onramp.cases.models import SupportCase
from onramp.cases.state import CaseState
from onramp.checks.models import CheckRun
from onramp.checks.service import CheckService, ChecksNotApplicableError
from onramp.entrypoints.seed import seed
from onramp.identity.principal import Principal
from onramp.identity.roles import Role
from onramp.marketplace import models as m
from onramp.marketplace.faults import Fault, FaultPlan, FaultyMarketplaceWriter, OutcomeUnknownError
from onramp.marketplace.reader import SqlMarketplaceReader
from onramp.marketplace.writer import SqlMarketplaceWriter
from onramp.policy.corpus import load_corpus
from onramp.policy.loader import load_documents
from onramp.proposals.models import Proposal
from onramp.proposals.service import ProposalService

NOW = datetime(2026, 10, 1, tzinfo=UTC)
CORPUS = Path(__file__).resolve().parents[3] / "policies"
OPERATOR = Actor.user("acme_operator")
Sessions = tuple[sessionmaker[Session], sessionmaker[Session]]


@pytest.fixture
def sessions(
    onramp_sessions: sessionmaker[Session], marketplace_sessions: sessionmaker[Session]
) -> Sessions:
    seed(onramp_sessions, marketplace_sessions, NOW)
    with onramp_sessions() as session, session.begin():
        load_documents(session, load_corpus(CORPUS))
    return onramp_sessions, marketplace_sessions


def _service(sessions: Sessions, plan: FaultPlan | None = None) -> CheckService:
    onramp, marketplace = sessions
    writer = SqlMarketplaceWriter(marketplace, clock=lambda: NOW)
    requester = EvidenceRequester(FaultyMarketplaceWriter(writer, plan) if plan else writer)
    return CheckService(onramp, SqlMarketplaceReader(marketplace), requester, lambda: NOW)


def _case_id(onramp: sessionmaker[Session], subject: str) -> str:
    with onramp() as session:
        return session.scalars(
            select(SupportCase.id).filter_by(tenant_id="acme", subject_id=subject)
        ).one()


def _state(onramp: sessionmaker[Session], case_id: str) -> str:
    with onramp() as session:
        return session.get_one(SupportCase, case_id).state


def test_conclusive_payout_becomes_a_pending_system_proposal(sessions: Sessions) -> None:
    onramp, _ = sessions
    case_id = _case_id(onramp, "acme_s_8821")

    run = _service(sessions).run("acme", case_id, OPERATOR)

    assert (run.outcome, run.policy_id, run.policy_version) == ("resolve", "payout", 12)
    assert _state(onramp, case_id) == CaseState.PROPOSED
    with onramp() as session:
        proposal = session.get_one(Proposal, run.proposal_id)
        assert (proposal.proposer_type, proposal.status) == ("system", "pending")
        assert proposal.action_params == {"amount_cents": 8400, "currency": "USD"}
        assert "policy_passage:payout_v12_section_4_3" in proposal.evidence_refs


def test_operator_accepts_check_proposal_into_approval(sessions: Sessions) -> None:
    onramp, _ = sessions
    case_id = _case_id(onramp, "acme_s_8821")
    run = _service(sessions).run("acme", case_id, OPERATOR)

    assert run.proposal_id is not None
    ProposalService(onramp, lambda: NOW).accept(
        Principal("acme", "acme_operator", Role.OPERATOR), run.proposal_id
    )

    assert _state(onramp, case_id) == CaseState.AWAITING_APPROVAL


def test_missing_tax_form_requests_it_and_waits(sessions: Sessions) -> None:
    onramp, marketplace = sessions
    case_id = _case_id(onramp, "acme_w_610")

    run = _service(sessions).run("acme", case_id, OPERATOR)

    assert run.missing_items == ["tax_form"]
    assert _state(onramp, case_id) == CaseState.WAITING_FOR_EVIDENCE
    with marketplace() as session:
        request = session.scalars(select(m.DocumentRequest)).one()
        assert (request.worker_id, request.document_kind) == ("acme_w_610", "tax_form")


def test_lost_response_on_evidence_request_does_not_ask_twice(sessions: Sessions) -> None:
    onramp, marketplace = sessions
    case_id = _case_id(onramp, "acme_w_610")
    flaky = _service(sessions, FaultPlan().inject("request_document", 1, Fault.LOST_RESPONSE))

    with pytest.raises(OutcomeUnknownError):
        flaky.run("acme", case_id, OPERATOR)
    assert _state(onramp, case_id) == CaseState.INITIAL_CHECKS

    _service(sessions).run("acme", case_id, OPERATOR)

    assert _state(onramp, case_id) == CaseState.WAITING_FOR_EVIDENCE
    with marketplace() as session:
        assert session.scalar(select(func.count()).select_from(m.DocumentRequest)) == 1


def test_checks_cannot_rerun_on_a_case_that_moved_on(sessions: Sessions) -> None:
    onramp, _ = sessions
    case_id = _case_id(onramp, "acme_s_8822")
    _service(sessions).run("acme", case_id, OPERATOR)

    with pytest.raises(ChecksNotApplicableError):
        _service(sessions).run("acme", case_id, OPERATOR)


def test_no_policy_in_force_escalates_with_the_error_recorded(sessions: Sessions) -> None:
    onramp, _ = sessions
    case_id = _case_id(onramp, "acme_s_8821")
    with onramp() as session, session.begin():
        session.get_one(SupportCase, case_id).incident_at = datetime(2025, 1, 1, tzinfo=UTC)

    run = _service(sessions).run("acme", case_id, OPERATOR)

    assert run.outcome == "error"
    assert "NoApplicablePolicyError" in (run.error or "")
    assert _state(onramp, case_id) == CaseState.ESCALATED
    with onramp() as session:
        assert session.scalar(select(func.count()).select_from(CheckRun)) == 1
