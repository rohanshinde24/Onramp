"""Deterministic execution of approved marketplace actions (ADR-008)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.actions.models import ExecutedAction, ExecutionStatus
from onramp.approvals.models import Approval, Decision
from onramp.audit.service import Actor, record_audit
from onramp.cases.escalation import EscalationReason
from onramp.cases.evidence import EvidenceUnavailableError, case_records
from onramp.cases.models import CaseType, SupportCase
from onramp.cases.resolutions import ACTION_RISK, ActionType, ResolutionCode, required_action
from onramp.cases.service import transition
from onramp.cases.state import CaseState
from onramp.checks.outcome import OutcomeKind
from onramp.checks.policy_context import PolicyContext, PolicyGapError
from onramp.checks.runner import MissingRecordError, policy_context, run_checks
from onramp.db.types import new_id
from onramp.events.contracts import ActionExecutedV1, ActionFailedV1
from onramp.events.outbox import emit
from onramp.identity.models import Tenant, User
from onramp.identity.roles import Permission, Role, has_permission
from onramp.marketplace.faults import OutcomeUnknownError
from onramp.marketplace.reader import MarketplaceReader
from onramp.marketplace.records import Record
from onramp.marketplace.writer import MarketplaceError, MarketplaceWriter
from onramp.policy.applicability import NoApplicablePolicyError
from onramp.proposals.content import ACTION_PARAMS, content_hash
from onramp.proposals.models import Proposal, ProposalStatus

Clock = Callable[[], datetime]

_APPROVAL_PERMISSION = {
    "medium": Permission.APPROVE_MEDIUM_RISK,
    "high": Permission.APPROVE_HIGH_RISK,
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GatewayRejectedError(Exception):
    """A deterministic guard rejected the action before a marketplace write."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class ActionExecutionFailedError(Exception):
    pass


class PostconditionFailedError(ActionExecutionFailedError):
    pass


@dataclass(frozen=True)
class _Prepared:
    action_id: str
    tenant_id: str
    case_id: str
    proposal_id: str
    approval_id: str
    action_type: ActionType
    idempotency_key: str
    request: dict[str, Any]


class ActionGateway:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        marketplace: MarketplaceReader,
        writer: MarketplaceWriter,
        clock: Clock = _utcnow,
        *,
        unknown_outcome_attempts: int = 3,
    ) -> None:
        if unknown_outcome_attempts < 1:
            raise ValueError("unknown_outcome_attempts must be positive")
        self._sessions = sessions
        self._marketplace = marketplace
        self._writer = writer
        self._clock = clock
        self._unknown_outcome_attempts = unknown_outcome_attempts

    def execute(self, tenant_id: str, proposal_id: str) -> ExecutedAction:
        try:
            prepared, completed = self._prepare(tenant_id, proposal_id)
        except GatewayRejectedError as exc:
            self._record_rejection(tenant_id, proposal_id, exc)
            raise
        if completed is not None:
            return completed

        try:
            result = self._call_marketplace(prepared)
        except MarketplaceError as exc:
            failed = self._finish_failure(
                prepared,
                ExecutionStatus.FAILED,
                "marketplace_rejected",
                str(exc),
                EscalationReason.ACTION_EXECUTION_FAILED,
            )
            if failed.status == ExecutionStatus.SUCCEEDED:
                return failed
            raise ActionExecutionFailedError(str(exc)) from exc

        if not self._postcondition(prepared, result):
            self._finish_failure(
                prepared,
                ExecutionStatus.UNVERIFIED,
                EscalationReason.POSTCONDITION_FAILED.value,
                "marketplace result did not satisfy the action postcondition",
                EscalationReason.POSTCONDITION_FAILED,
            )
            raise PostconditionFailedError(prepared.action_id)
        return self._finish_success(prepared, result)

    def _record_rejection(
        self, tenant_id: str, proposal_id: str, rejection: GatewayRejectedError
    ) -> None:
        """Make a guard rejection visible without manufacturing an execution record."""
        now = self._clock()
        with self._sessions() as session, session.begin():
            proposal = session.scalar(
                select(Proposal).filter_by(tenant_id=tenant_id, id=proposal_id)
            )
            if proposal is None:
                return
            case = session.scalar(
                select(SupportCase)
                .filter_by(tenant_id=tenant_id, id=proposal.case_id)
                .with_for_update()
            )
            if case is None:
                return
            record_audit(
                session,
                tenant_id=tenant_id,
                actor=Actor.system(),
                subject_type="case",
                subject_id=case.id,
                action="action.rejected",
                occurred_at=now,
                details={"proposal_id": proposal.id, "reason_code": rejection.code},
            )
            if case.state == CaseState.EXECUTING:
                reason = (
                    EscalationReason.AMOUNT_OVER_LIMIT
                    if rejection.code == "amount_over_limit"
                    else EscalationReason.INVALID_PROPOSAL
                )
                case.escalation_reason = reason.value
                transition(
                    session,
                    case,
                    CaseState.ESCALATED,
                    actor=Actor.system(),
                    occurred_at=now,
                    reason=reason.value,
                    details={"proposal_id": proposal.id, "failure_code": rejection.code},
                )

    def _prepare(self, tenant_id: str, proposal_id: str) -> tuple[_Prepared, ExecutedAction | None]:
        with self._sessions() as session, session.begin():
            proposal = session.scalar(
                select(Proposal).filter_by(tenant_id=tenant_id, id=proposal_id).with_for_update()
            )
            if proposal is None:
                raise GatewayRejectedError("proposal_not_found")
            case = session.scalar(
                select(SupportCase)
                .filter_by(tenant_id=tenant_id, id=proposal.case_id)
                .with_for_update()
            )
            if case is None:
                raise GatewayRejectedError("case_not_found")

            existing = session.scalar(
                select(ExecutedAction)
                .filter_by(tenant_id=tenant_id, proposal_id=proposal.id)
                .with_for_update()
            )
            if existing is not None and existing.status == ExecutionStatus.SUCCEEDED:
                return _from_action(existing), existing
            if existing is not None and existing.status in {
                ExecutionStatus.FAILED,
                ExecutionStatus.UNVERIFIED,
            }:
                raise GatewayRejectedError("action_terminal", existing.status)

            approval, action_type = self._guard_approval(session, case, proposal)
            request = self._guard_action(session, case, proposal, action_type, existing is not None)
            key = f"action:{proposal.id}:{action_type.value}"
            if existing is None:
                existing = ExecutedAction(
                    id=new_id("act"),
                    tenant_id=tenant_id,
                    case_id=case.id,
                    proposal_id=proposal.id,
                    approval_id=approval.id,
                    action_type=action_type.value,
                    idempotency_key=key,
                    request=request,
                    status=ExecutionStatus.EXECUTING.value,
                    created_at=self._clock(),
                )
                session.add(existing)
                session.flush()
                record_audit(
                    session,
                    tenant_id=tenant_id,
                    actor=Actor.system(),
                    subject_type="case",
                    subject_id=case.id,
                    action="action.executing",
                    occurred_at=existing.created_at,
                    details={
                        "action_id": existing.id,
                        "proposal_id": proposal.id,
                        "approval_id": approval.id,
                        "action_type": action_type.value,
                        "idempotency_key": key,
                    },
                )
            elif existing.request != request or existing.idempotency_key != key:
                raise GatewayRejectedError("execution_record_mismatch")
            return _from_action(existing), None

    def _guard_approval(
        self, session: Session, case: SupportCase, proposal: Proposal
    ) -> tuple[Approval, ActionType]:
        if case.state != CaseState.EXECUTING:
            raise GatewayRejectedError("illegal_case_state", case.state)
        if proposal.status != ProposalStatus.ACCEPTED or proposal.action_type is None:
            raise GatewayRejectedError("proposal_not_executable")
        try:
            action_type = ActionType(proposal.action_type)
            resolution = ResolutionCode(proposal.resolution_code)
        except ValueError as exc:
            raise GatewayRejectedError("proposal_catalog_mismatch") from exc
        if required_action(CaseType(case.case_type), resolution) is not action_type:
            raise GatewayRejectedError("proposal_catalog_mismatch")
        expected_risk = ACTION_RISK[action_type].value
        if proposal.risk_class != expected_risk:
            raise GatewayRejectedError("risk_mismatch")
        try:
            ACTION_PARAMS[action_type].model_validate(proposal.action_params)
        except ValidationError as exc:
            raise GatewayRejectedError("invalid_action_params") from exc
        expected_hash = content_hash(
            case_id=case.id,
            resolution_code=proposal.resolution_code,
            action_type=proposal.action_type,
            action_params=proposal.action_params,
            evidence_refs=proposal.evidence_refs,
        )
        if proposal.content_hash != expected_hash:
            raise GatewayRejectedError("proposal_hash_mismatch")

        approval = session.scalar(
            select(Approval).filter_by(tenant_id=case.tenant_id, proposal_id=proposal.id)
        )
        if approval is None or approval.decision != Decision.APPROVED:
            raise GatewayRejectedError("approval_missing")
        if approval.content_hash != expected_hash:
            raise GatewayRejectedError("approval_hash_mismatch")
        if approval.risk_class != expected_risk:
            raise GatewayRejectedError("approval_risk_mismatch")
        approver = session.scalar(
            select(User).filter_by(tenant_id=case.tenant_id, id=approval.approver_id)
        )
        if approver is None:
            raise GatewayRejectedError("approver_not_found")
        try:
            role = Role(approver.role)
        except ValueError as exc:
            raise GatewayRejectedError("approver_role_invalid") from exc
        if not has_permission(role, _APPROVAL_PERMISSION[expected_risk]):
            raise GatewayRejectedError("approver_not_authorized")
        if proposal.proposer_type == "user" and proposal.proposer_id == approver.id:
            raise GatewayRejectedError("self_approval")
        if action_type is ActionType.PAYOUT_ADJUSTMENT:
            amount = proposal.action_params.get("amount_cents")
            limit = session.get_one(Tenant, case.tenant_id).payout_approval_limit_cents
            if not isinstance(amount, int) or amount > limit:
                raise GatewayRejectedError("amount_over_limit")
        return approval, action_type

    def _guard_action(
        self,
        session: Session,
        case: SupportCase,
        proposal: Proposal,
        action_type: ActionType,
        resuming: bool,
    ) -> dict[str, Any]:
        try:
            policy = policy_context(session, case)
        except (NoApplicablePolicyError, PolicyGapError) as exc:
            raise GatewayRejectedError("policy_not_applicable", str(exc)) from exc
        reachable = self._reachable_refs(case)
        policy_refs = set(policy.passages.values())
        policy_refs.update(str(p["passage_id"]) for p in policy.parameters.values())
        reachable.update(f"policy_passage:{passage}" for passage in policy_refs)
        invalid = sorted(set(proposal.evidence_refs) - reachable)
        if invalid:
            raise GatewayRejectedError("evidence_not_reachable", ", ".join(invalid))

        if action_type is ActionType.PAYOUT_ADJUSTMENT:
            request = self._payout_request(case, proposal, policy)
        elif action_type is ActionType.ACTIVATE_WORKER:
            worker = self._marketplace.get_worker(case.tenant_id, case.worker_id)
            if worker is None:
                raise GatewayRejectedError("worker_not_found")
            activated_by_this_action = resuming and worker.status == "active"
            if worker.status != "pending_onboarding" and not activated_by_this_action:
                raise GatewayRejectedError("worker_not_activatable", worker.status)
            if not activated_by_this_action:
                self._require_onboarding_complete(session, case)
            request = {"worker_id": worker.id}
        else:
            review = self._marketplace.get_quality_review(case.tenant_id, case.subject_id)
            if review is None:
                raise GatewayRejectedError("review_not_found")
            already_done = review.result == "pass" and review.overturned_at is not None
            if review.result != "fail" and not (resuming and already_done):
                raise GatewayRejectedError("review_not_overturnable", review.result)
            request = {"review_id": review.id}

        proposed = (
            ACTION_PARAMS[action_type]
            .model_validate(proposal.action_params)
            .model_dump(mode="json")
        )
        derived_params = {k: v for k, v in request.items() if k in proposed}
        if proposed != derived_params:
            raise GatewayRejectedError("action_params_mismatch")
        return request

    def _require_onboarding_complete(self, session: Session, case: SupportCase) -> None:
        """Activation needs every verification the policy in force requires, passed now.

        The proposal's resolution is not evidence of that: a model can be talked into it and a
        reviewer can miss it. The deterministic onboarding check decides.
        """
        try:
            outcome = run_checks(session, case, self._marketplace, self._clock())
        except MissingRecordError as exc:
            raise GatewayRejectedError("worker_not_found", str(exc)) from exc
        if (outcome.kind, outcome.resolution_code) != (
            OutcomeKind.RESOLVE,
            ResolutionCode.REQUIREMENTS_MET,
        ):
            raise GatewayRejectedError(
                "onboarding_requirements_not_met", ", ".join(outcome.reason_codes)
            )

    def _reachable_refs(self, case: SupportCase) -> set[str]:
        try:
            return {record.ref for record in case_records(self._marketplace, case)}
        except EvidenceUnavailableError as exc:
            raise GatewayRejectedError(exc.code) from exc

    def _payout_request(
        self, case: SupportCase, proposal: Proposal, policy: PolicyContext
    ) -> dict[str, Any]:
        tenant = case.tenant_id
        submission = self._marketplace.get_submission(tenant, case.subject_id)
        if submission is None:
            raise GatewayRejectedError("submission_not_found")
        assignment = self._marketplace.get_assignment(tenant, submission.assignment_id)
        if assignment is None or assignment.worker_id != case.worker_id:
            raise GatewayRejectedError("subject_identity_mismatch")
        task = self._marketplace.get_task(tenant, assignment.task_id)
        if task is None:
            raise GatewayRejectedError("task_not_found")
        reviews = self._marketplace.get_quality_reviews(tenant, submission.id)
        latest = max(reviews, key=lambda review: review.reviewed_at) if reviews else None
        accepted = submission.status == "accepted" or (
            latest is not None and latest.result == "pass" and latest.overturned_at is not None
        )
        if not accepted or latest is None or latest.result != "pass":
            raise GatewayRejectedError("payout_not_eligible")

        payments = [
            payment
            for payment in self._marketplace.get_payments_for_submission(tenant, submission.id)
            if payment.payment_kind == "task_payout"
        ]
        if len(payments) > 1:
            raise GatewayRejectedError("duplicate_task_payments")
        settled = 0
        due = submission.submitted_at + timedelta(days=1)
        if payments:
            payment = payments[0]
            if payment.worker_id != case.worker_id or payment.currency != task.currency:
                raise GatewayRejectedError("payment_identity_or_currency_mismatch")
            events = self._marketplace.get_payment_events(tenant, payment.id)
            settled = sum(
                event.amount_cents for event in events if event.event_type == "settlement_succeeded"
            )
            authorized = [event.occurred_at for event in events if event.event_type == "authorized"]
            due = min(authorized) if authorized else payment.created_at
        due += timedelta(days=int(policy.value("settlement_window_days")))
        if self._clock() < due:
            raise GatewayRejectedError("settlement_window_open")
        amount = task.payout_cents - settled
        if amount <= 0:
            raise GatewayRejectedError("nothing_owed")
        if proposal.resolution_code == ResolutionCode.MISSING_PAYOUT and settled != 0:
            raise GatewayRejectedError("resolution_amount_mismatch")
        if proposal.resolution_code == ResolutionCode.UNDERPAID and settled <= 0:
            raise GatewayRejectedError("resolution_amount_mismatch")
        expected = {"amount_cents": amount, "currency": task.currency}
        if proposal.action_params != expected:
            raise GatewayRejectedError("payout_recomputation_mismatch")
        return {"worker_id": case.worker_id, **expected}

    def _call_marketplace(self, prepared: _Prepared) -> Record:
        for attempt in range(self._unknown_outcome_attempts):
            try:
                request = prepared.request
                if prepared.action_type is ActionType.PAYOUT_ADJUSTMENT:
                    return self._writer.issue_adjustment(
                        prepared.tenant_id,
                        str(request["worker_id"]),
                        int(request["amount_cents"]),
                        str(request["currency"]),
                        idempotency_key=prepared.idempotency_key,
                    )
                if prepared.action_type is ActionType.ACTIVATE_WORKER:
                    return self._writer.activate_worker(
                        prepared.tenant_id,
                        str(request["worker_id"]),
                        idempotency_key=prepared.idempotency_key,
                    )
                return self._writer.overturn_review(
                    prepared.tenant_id,
                    str(request["review_id"]),
                    idempotency_key=prepared.idempotency_key,
                )
            except OutcomeUnknownError:
                if attempt + 1 == self._unknown_outcome_attempts:
                    raise
        raise AssertionError("unreachable")

    def _postcondition(self, prepared: _Prepared, result: Record) -> bool:
        request = prepared.request
        if prepared.action_type is ActionType.PAYOUT_ADJUSTMENT:
            payment = self._marketplace.get_payment(prepared.tenant_id, result.id)
            return payment is not None and all(
                (
                    payment.payment_kind == "adjustment",
                    payment.status == "settled",
                    payment.worker_id == request["worker_id"],
                    payment.amount_cents == request["amount_cents"],
                    payment.currency == request["currency"],
                )
            )
        if prepared.action_type is ActionType.ACTIVATE_WORKER:
            worker = self._marketplace.get_worker(prepared.tenant_id, str(request["worker_id"]))
            return worker is not None and worker.status == "active"
        review = self._marketplace.get_quality_review(prepared.tenant_id, str(request["review_id"]))
        return review is not None and review.result == "pass" and review.overturned_at is not None

    def _finish_success(self, prepared: _Prepared, result: Record) -> ExecutedAction:
        now = self._clock()
        stored_result = result.model_dump(mode="json")
        with self._sessions() as session, session.begin():
            action = session.scalar(
                select(ExecutedAction)
                .filter_by(tenant_id=prepared.tenant_id, id=prepared.action_id)
                .with_for_update()
            )
            if action is None:
                raise RuntimeError(f"action {prepared.action_id} disappeared")
            if action.status == ExecutionStatus.SUCCEEDED:
                return action
            if action.status != ExecutionStatus.EXECUTING:
                raise ActionExecutionFailedError(action.status)
            case = session.scalar(
                select(SupportCase)
                .filter_by(tenant_id=prepared.tenant_id, id=prepared.case_id)
                .with_for_update()
            )
            if case is None or case.state != CaseState.EXECUTING:
                raise ActionExecutionFailedError("case no longer executing")
            proposal = session.get_one(Proposal, prepared.proposal_id)
            action.status = ExecutionStatus.SUCCEEDED.value
            action.result = stored_result
            action.completed_at = now
            record_audit(
                session,
                tenant_id=case.tenant_id,
                actor=Actor.system(),
                subject_type="case",
                subject_id=case.id,
                action="action.executed",
                occurred_at=now,
                details={
                    "action_id": action.id,
                    "proposal_id": proposal.id,
                    "action_type": action.action_type,
                    "result": stored_result,
                },
            )
            emit(
                session,
                event_type="action.executed",
                tenant_id=case.tenant_id,
                aggregate_id=case.id,
                payload=ActionExecutedV1(
                    action_id=action.id,
                    proposal_id=proposal.id,
                    approval_id=action.approval_id,
                    action_type=action.action_type,
                    result=stored_result,
                ),
                occurred_at=now,
            )
            case.resolution_code = proposal.resolution_code
            transition(
                session,
                case,
                CaseState.VERIFYING,
                actor=Actor.system(),
                occurred_at=now,
                reason="action_executed",
                details={"action_id": action.id},
            )
            transition(
                session,
                case,
                CaseState.RESOLVED,
                actor=Actor.system(),
                occurred_at=now,
                reason="postcondition_verified",
                details={"action_id": action.id},
            )
            return action

    def _finish_failure(
        self,
        prepared: _Prepared,
        status: ExecutionStatus,
        code: str,
        message: str,
        escalation: EscalationReason,
    ) -> ExecutedAction:
        now = self._clock()
        with self._sessions() as session, session.begin():
            action = session.scalar(
                select(ExecutedAction)
                .filter_by(tenant_id=prepared.tenant_id, id=prepared.action_id)
                .with_for_update()
            )
            if action is None:
                raise RuntimeError(f"action {prepared.action_id} disappeared")
            if action.status != ExecutionStatus.EXECUTING:
                return action
            case = session.scalar(
                select(SupportCase)
                .filter_by(tenant_id=prepared.tenant_id, id=prepared.case_id)
                .with_for_update()
            )
            if case is None:
                raise RuntimeError(f"case {prepared.case_id} disappeared")
            action.status = status.value
            action.failure_code = code
            action.failure_message = message
            action.completed_at = now
            record_audit(
                session,
                tenant_id=case.tenant_id,
                actor=Actor.system(),
                subject_type="case",
                subject_id=case.id,
                action="action.failed",
                occurred_at=now,
                details={"action_id": action.id, "reason_code": code, "status": status.value},
            )
            emit(
                session,
                event_type="action.failed",
                tenant_id=case.tenant_id,
                aggregate_id=case.id,
                payload=ActionFailedV1(
                    action_id=action.id,
                    proposal_id=action.proposal_id,
                    approval_id=action.approval_id,
                    action_type=action.action_type,
                    status=status.value,
                    reason_code=code,
                ),
                occurred_at=now,
            )
            if case.state == CaseState.EXECUTING:
                case.escalation_reason = escalation.value
                transition(
                    session,
                    case,
                    CaseState.ESCALATED,
                    actor=Actor.system(),
                    occurred_at=now,
                    reason=escalation.value,
                    details={"action_id": action.id, "failure_code": code},
                )
            return action


def _from_action(action: ExecutedAction) -> _Prepared:
    return _Prepared(
        action_id=action.id,
        tenant_id=action.tenant_id,
        case_id=action.case_id,
        proposal_id=action.proposal_id,
        approval_id=action.approval_id,
        action_type=ActionType(action.action_type),
        idempotency_key=action.idempotency_key,
        request=action.request,
    )
