from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from onramp.actions.evidence import EvidenceRequester
from onramp.actions.gateway import (
    ActionExecutionFailedError,
    ActionGateway,
    GatewayRejectedError,
    PostconditionFailedError,
)
from onramp.actions.models import ExecutedAction, ExecutionStatus
from onramp.approvals.models import Approval, Decision
from onramp.approvals.service import ApprovalService
from onramp.audit.models import AuditEvent
from onramp.audit.service import Actor
from onramp.cases.models import SupportCase
from onramp.cases.state import CaseState
from onramp.checks.service import CheckService
from onramp.entrypoints.seed import seed
from onramp.events.models import OutboxEvent
from onramp.identity.models import Tenant, User
from onramp.identity.principal import Principal
from onramp.identity.roles import Role
from onramp.marketplace import models as marketplace_models
from onramp.marketplace.faults import Fault, FaultPlan, FaultyMarketplaceWriter
from onramp.marketplace.reader import SqlMarketplaceReader
from onramp.marketplace.writer import RejectedError, SqlMarketplaceWriter
from onramp.policy.corpus import load_corpus
from onramp.policy.loader import load_documents
from onramp.proposals.content import content_hash
from onramp.proposals.models import Proposal
from onramp.proposals.service import ProposalService

NOW = datetime(2026, 10, 1, tzinfo=UTC)
CORPUS = Path(__file__).resolve().parents[3] / "policies"
OPERATOR = Principal("acme", "acme_operator", Role.OPERATOR)
LEAD = Principal("acme", "acme_lead", Role.LEAD)
Sessions = tuple[sessionmaker[Session], sessionmaker[Session]]


@pytest.fixture
def sessions(
    onramp_sessions: sessionmaker[Session], marketplace_sessions: sessionmaker[Session]
) -> Sessions:
    _seed(onramp_sessions, marketplace_sessions)
    return onramp_sessions, marketplace_sessions


def _seed(onramp: sessionmaker[Session], marketplace: sessionmaker[Session]) -> None:
    seed(onramp, marketplace, NOW)
    with onramp() as session, session.begin():
        load_documents(session, load_corpus(CORPUS))


def _approved_payout(sessions: Sessions) -> tuple[str, str]:
    return _approved_case(sessions, "acme_s_8821")


def _approved_case(sessions: Sessions, subject_id: str) -> tuple[str, str]:
    onramp, marketplace = sessions
    reader = SqlMarketplaceReader(marketplace)
    writer = SqlMarketplaceWriter(marketplace, lambda: NOW)
    checks = CheckService(onramp, reader, EvidenceRequester(writer), lambda: NOW)
    with onramp() as session:
        case_id = session.scalars(
            select(SupportCase.id).filter_by(tenant_id="acme", subject_id=subject_id)
        ).one()
    run = checks.run("acme", case_id, Actor.user(OPERATOR.user_id))
    assert run.proposal_id is not None
    ProposalService(onramp, lambda: NOW).accept(OPERATOR, run.proposal_id)
    with onramp() as session:
        proposal = session.get_one(Proposal, run.proposal_id)
        proposal_hash = proposal.content_hash
    ApprovalService(onramp, lambda: NOW).decide(
        LEAD,
        run.proposal_id,
        Decision.APPROVED,
        content_hash=proposal_hash,
        note="verified",
    )
    return case_id, run.proposal_id


def _gateway(
    sessions: Sessions,
    *,
    writer: Any | None = None,
    reader: SqlMarketplaceReader | None = None,
) -> ActionGateway:
    onramp, marketplace = sessions
    actual_reader = reader or SqlMarketplaceReader(marketplace)
    actual_writer = writer or SqlMarketplaceWriter(marketplace, lambda: NOW)
    return ActionGateway(onramp, actual_reader, actual_writer, lambda: NOW)


def _adjustment_count(marketplace: sessionmaker[Session]) -> int:
    with marketplace() as session:
        return (
            session.scalar(
                select(func.count())
                .select_from(marketplace_models.Payment)
                .filter_by(tenant_id="acme", kind="adjustment")
            )
            or 0
        )


def _rehash(proposal: Proposal) -> str:
    return content_hash(
        case_id=proposal.case_id,
        resolution_code=proposal.resolution_code,
        action_type=proposal.action_type,
        action_params=proposal.action_params,
        evidence_refs=proposal.evidence_refs,
    )


def test_success_is_recorded_verified_emitted_and_replay_safe(sessions: Sessions) -> None:
    onramp, marketplace = sessions
    case_id, proposal_id = _approved_payout(sessions)
    gateway = _gateway(sessions)

    first = gateway.execute("acme", proposal_id)
    replay = gateway.execute("acme", proposal_id)

    assert first.id == replay.id
    assert first.status == ExecutionStatus.SUCCEEDED
    assert _adjustment_count(marketplace) == 1
    with onramp() as session:
        case = session.get_one(SupportCase, case_id)
        assert (case.state, case.resolution_code) == (CaseState.RESOLVED, "MISSING_PAYOUT")
        actions = session.scalars(
            select(AuditEvent.action).filter_by(subject_id=case_id).order_by(AuditEvent.id)
        ).all()
        assert "action.executing" in actions
        assert "action.executed" in actions
        event_types = session.scalars(
            select(OutboxEvent.event_type).filter_by(aggregate_id=case_id).order_by(OutboxEvent.id)
        ).all()
        assert "action.executed" in event_types
        assert event_types[-1] == "case.resolved"


@pytest.mark.parametrize(
    "evidence_ref",
    ["worker:acme_w_493", "payment:acme_pay_9203", "worker:globex_w_492"],
)
def test_cross_subject_or_tenant_evidence_is_rejected_without_a_write(
    sessions: Sessions, evidence_ref: str
) -> None:
    onramp, marketplace = sessions
    _, proposal_id = _approved_payout(sessions)
    with onramp() as session, session.begin():
        proposal = session.get_one(Proposal, proposal_id)
        proposal.evidence_refs = [*proposal.evidence_refs, evidence_ref]
        proposal.content_hash = _rehash(proposal)
        session.scalars(
            select(Approval).filter_by(proposal_id=proposal_id)
        ).one().content_hash = proposal.content_hash

    with pytest.raises(GatewayRejectedError) as exc:
        _gateway(sessions).execute("acme", proposal_id)

    assert exc.value.code == "evidence_not_reachable"
    assert _adjustment_count(marketplace) == 0


@pytest.mark.parametrize(
    "params",
    [
        {"amount_cents": 1, "currency": "USD"},
        {"amount_cents": 8400, "currency": "EUR"},
    ],
)
def test_payout_amount_and_currency_are_recomputed(
    sessions: Sessions, params: dict[str, Any]
) -> None:
    onramp, marketplace = sessions
    _, proposal_id = _approved_payout(sessions)
    with onramp() as session, session.begin():
        proposal = session.get_one(Proposal, proposal_id)
        proposal.action_params = params
        proposal.content_hash = _rehash(proposal)
        session.scalars(
            select(Approval).filter_by(proposal_id=proposal_id)
        ).one().content_hash = proposal.content_hash

    with pytest.raises(GatewayRejectedError) as exc:
        _gateway(sessions).execute("acme", proposal_id)

    assert exc.value.code == "payout_recomputation_mismatch"
    assert _adjustment_count(marketplace) == 0
    with onramp() as session:
        proposal = session.get_one(Proposal, proposal_id)
        case = session.get_one(SupportCase, proposal.case_id)
        assert (case.state, case.escalation_reason) == (
            CaseState.ESCALATED,
            "invalid_proposal",
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .filter_by(subject_id=case.id, action="action.rejected")
            )
            == 1
        )


@pytest.mark.parametrize(
    ("tampering", "expected_code"),
    [
        ("proposal_hash", "proposal_hash_mismatch"),
        ("approval_decision", "approval_missing"),
        ("risk", "risk_mismatch"),
        ("catalog", "proposal_catalog_mismatch"),
        ("self_approval", "self_approval"),
    ],
)
def test_proposal_approval_and_catalog_guards_reject_before_writing(
    sessions: Sessions, tampering: str, expected_code: str
) -> None:
    onramp, marketplace = sessions
    _, proposal_id = _approved_payout(sessions)
    with onramp() as session, session.begin():
        proposal = session.get_one(Proposal, proposal_id)
        approval = session.scalars(select(Approval).filter_by(proposal_id=proposal_id)).one()
        if tampering == "proposal_hash":
            proposal.content_hash = "f" * 64
        elif tampering == "approval_decision":
            approval.decision = Decision.REJECTED.value
        elif tampering == "risk":
            proposal.risk_class = "medium"
        elif tampering == "catalog":
            proposal.action_type = "ACTIVATE_WORKER"
            proposal.action_params = {}
            proposal.risk_class = "medium"
            proposal.content_hash = _rehash(proposal)
            approval.content_hash = proposal.content_hash
            approval.risk_class = "medium"
        else:
            proposal.proposer_type = "user"
            proposal.proposer_id = approval.approver_id

    with pytest.raises(GatewayRejectedError) as exc:
        _gateway(sessions).execute("acme", proposal_id)

    assert exc.value.code == expected_code
    assert _adjustment_count(marketplace) == 0


def test_stale_approval_hash_is_rejected_without_a_write(sessions: Sessions) -> None:
    onramp, marketplace = sessions
    _, proposal_id = _approved_payout(sessions)
    with onramp() as session, session.begin():
        session.scalars(select(Approval).filter_by(proposal_id=proposal_id)).one().content_hash = (
            "0" * 64
        )

    with pytest.raises(GatewayRejectedError) as exc:
        _gateway(sessions).execute("acme", proposal_id)

    assert exc.value.code == "approval_hash_mismatch"
    assert _adjustment_count(marketplace) == 0


def test_approver_authorization_is_rechecked(sessions: Sessions) -> None:
    onramp, marketplace = sessions
    _, proposal_id = _approved_payout(sessions)
    with onramp() as session, session.begin():
        session.get_one(User, LEAD.user_id).role = Role.OPERATOR.value

    with pytest.raises(GatewayRejectedError) as exc:
        _gateway(sessions).execute("acme", proposal_id)

    assert exc.value.code == "approver_not_authorized"
    assert _adjustment_count(marketplace) == 0


def test_trusted_tenant_scope_hides_another_tenants_proposal(sessions: Sessions) -> None:
    _, marketplace = sessions
    _, proposal_id = _approved_payout(sessions)

    with pytest.raises(GatewayRejectedError) as exc:
        _gateway(sessions).execute("globex", proposal_id)

    assert exc.value.code == "proposal_not_found"
    assert _adjustment_count(marketplace) == 0


def test_subject_identity_is_resolved_from_the_case_and_marketplace(sessions: Sessions) -> None:
    onramp, marketplace = sessions
    case_id, proposal_id = _approved_payout(sessions)
    with onramp() as session, session.begin():
        session.get_one(SupportCase, case_id).worker_id = "acme_w_493"

    with pytest.raises(GatewayRejectedError) as exc:
        _gateway(sessions).execute("acme", proposal_id)

    assert exc.value.code == "subject_identity_mismatch"
    assert _adjustment_count(marketplace) == 0


def test_policy_in_force_is_rechecked(sessions: Sessions) -> None:
    onramp, marketplace = sessions
    case_id, proposal_id = _approved_payout(sessions)
    with onramp() as session, session.begin():
        session.get_one(SupportCase, case_id).incident_at = datetime(2020, 1, 1, tzinfo=UTC)

    with pytest.raises(GatewayRejectedError) as exc:
        _gateway(sessions).execute("acme", proposal_id)

    assert exc.value.code == "policy_not_applicable"
    assert _adjustment_count(marketplace) == 0


def test_current_payout_limit_is_rechecked(sessions: Sessions) -> None:
    onramp, marketplace = sessions
    _, proposal_id = _approved_payout(sessions)
    with onramp() as session, session.begin():
        session.get_one(Tenant, "acme").payout_approval_limit_cents = 100

    with pytest.raises(GatewayRejectedError) as exc:
        _gateway(sessions).execute("acme", proposal_id)

    assert exc.value.code == "amount_over_limit"
    assert _adjustment_count(marketplace) == 0


def test_illegal_execution_state_is_rejected(sessions: Sessions) -> None:
    onramp, marketplace = sessions
    case_id, proposal_id = _approved_payout(sessions)
    with onramp() as session, session.begin():
        session.get_one(SupportCase, case_id).state = CaseState.AWAITING_APPROVAL.value

    with pytest.raises(GatewayRejectedError) as exc:
        _gateway(sessions).execute("acme", proposal_id)

    assert exc.value.code == "illegal_case_state"
    assert _adjustment_count(marketplace) == 0


def test_crash_after_record_persistence_resumes_with_the_same_action(sessions: Sessions) -> None:
    onramp, marketplace = sessions
    _, proposal_id = _approved_payout(sessions)
    gateway = _gateway(sessions)

    prepared, completed = gateway._prepare("acme", proposal_id)
    assert completed is None
    with onramp() as session:
        stored = session.get_one(ExecutedAction, prepared.action_id)
        assert stored.status == ExecutionStatus.EXECUTING

    resumed = _gateway(sessions).execute("acme", proposal_id)

    assert resumed.id == prepared.action_id
    assert resumed.status == ExecutionStatus.SUCCEEDED
    assert _adjustment_count(marketplace) == 1


def test_lost_response_retries_the_same_key_and_mutates_once(sessions: Sessions) -> None:
    _, marketplace = sessions
    _, proposal_id = _approved_payout(sessions)
    inner = SqlMarketplaceWriter(marketplace, lambda: NOW)
    flaky = FaultyMarketplaceWriter(
        inner, FaultPlan().inject("issue_adjustment", 1, Fault.LOST_RESPONSE)
    )

    result = _gateway(sessions, writer=flaky).execute("acme", proposal_id)

    assert result.status == ExecutionStatus.SUCCEEDED
    assert flaky.plan.calls["issue_adjustment"] == 2
    assert _adjustment_count(marketplace) == 1


class _MissingPaymentReader(SqlMarketplaceReader):
    def get_payment(self, tenant_id: str, payment_id: str) -> None:
        return None


class _RejectingWriter(SqlMarketplaceWriter):
    def issue_adjustment(
        self,
        tenant_id: str,
        worker_id: str,
        amount_cents: int,
        currency: str,
        *,
        idempotency_key: str,
    ) -> Any:
        raise RejectedError("account closed")


def test_postcondition_failure_is_stored_audited_emitted_and_escalated(
    sessions: Sessions,
) -> None:
    onramp, marketplace = sessions
    case_id, proposal_id = _approved_payout(sessions)
    reader = _MissingPaymentReader(marketplace)

    with pytest.raises(PostconditionFailedError):
        _gateway(sessions, reader=reader).execute("acme", proposal_id)

    assert _adjustment_count(marketplace) == 1
    with onramp() as session:
        action = session.scalars(select(ExecutedAction).filter_by(proposal_id=proposal_id)).one()
        assert (action.status, action.failure_code) == (
            ExecutionStatus.UNVERIFIED,
            "postcondition_failed",
        )
        case = session.get_one(SupportCase, case_id)
        assert (case.state, case.escalation_reason) == (
            CaseState.ESCALATED,
            "postcondition_failed",
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .filter_by(subject_id=case_id, action="action.failed")
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .filter_by(aggregate_id=case_id, event_type="action.failed")
            )
            == 1
        )


def test_terminal_marketplace_failure_is_stored_and_escalated(sessions: Sessions) -> None:
    onramp, marketplace = sessions
    case_id, proposal_id = _approved_payout(sessions)

    with pytest.raises(ActionExecutionFailedError, match="account closed"):
        _gateway(sessions, writer=_RejectingWriter(marketplace)).execute("acme", proposal_id)

    assert _adjustment_count(marketplace) == 0
    with onramp() as session:
        action = session.scalars(select(ExecutedAction).filter_by(proposal_id=proposal_id)).one()
        assert (action.status, action.failure_code) == (
            ExecutionStatus.FAILED,
            "marketplace_rejected",
        )
        case = session.get_one(SupportCase, case_id)
        assert (case.state, case.escalation_reason) == (
            CaseState.ESCALATED,
            "action_execution_failed",
        )


@pytest.mark.parametrize(
    ("subject_id", "expected_action"),
    [
        ("acme_w_610", "ACTIVATE_WORKER"),
        ("acme_qr_4283", "OVERTURN_QUALITY_REVIEW"),
    ],
)
def test_nonfinancial_actions_execute_through_the_same_gateway(
    sessions: Sessions, subject_id: str, expected_action: str
) -> None:
    onramp, marketplace = sessions
    if expected_action == "ACTIVATE_WORKER":
        with marketplace() as session, session.begin():
            session.add(
                marketplace_models.VerificationRecord(
                    id="acme_v_610c",
                    tenant_id="acme",
                    worker_id="acme_w_610",
                    kind="tax_form",
                    status="passed",
                    completed_at=NOW - timedelta(days=1),
                )
            )
    case_id, proposal_id = _approved_case(sessions, subject_id)

    action = _gateway(sessions).execute("acme", proposal_id)

    assert (action.action_type, action.status) == (expected_action, ExecutionStatus.SUCCEEDED)
    with onramp() as session:
        assert session.get_one(SupportCase, case_id).state == CaseState.RESOLVED
    reader = SqlMarketplaceReader(marketplace)
    if expected_action == "ACTIVATE_WORKER":
        worker = reader.get_worker("acme", subject_id)
        assert worker is not None
        assert worker.status == "active"
    else:
        review = reader.get_quality_review("acme", subject_id)
        assert review is not None
        assert review.result == "pass"
        assert review.overturned_at is not None


def test_activation_rechecks_onboarding_requirements_at_execution(sessions: Sessions) -> None:
    onramp, marketplace = sessions
    with marketplace() as session, session.begin():
        session.add(
            marketplace_models.VerificationRecord(
                id="acme_v_610c",
                tenant_id="acme",
                worker_id="acme_w_610",
                kind="tax_form",
                status="passed",
                completed_at=NOW - timedelta(days=1),
            )
        )
    case_id, proposal_id = _approved_case(sessions, "acme_w_610")
    with marketplace() as session, session.begin():
        session.get_one(marketplace_models.VerificationRecord, "acme_v_610c").status = "expired"

    with pytest.raises(GatewayRejectedError) as rejected:
        _gateway(sessions).execute("acme", proposal_id)

    assert rejected.value.code == "onboarding_requirements_not_met"
    worker = SqlMarketplaceReader(marketplace).get_worker("acme", "acme_w_610")
    assert worker is not None
    assert worker.status == "pending_onboarding"
    with onramp() as session:
        case = session.get_one(SupportCase, case_id)
        assert (case.state, case.escalation_reason) == (CaseState.ESCALATED, "invalid_proposal")


def test_concurrent_execution_produces_one_business_mutation(
    onramp_committed: sessionmaker[Session], marketplace_committed: sessionmaker[Session]
) -> None:
    sessions = (onramp_committed, marketplace_committed)
    _seed(*sessions)
    _, proposal_id = _approved_payout(sessions)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: _gateway(sessions).execute("acme", proposal_id), range(2))
        )

    assert results[0].id == results[1].id
    assert {result.status for result in results} == {ExecutionStatus.SUCCEEDED}
    assert _adjustment_count(marketplace_committed) == 1
