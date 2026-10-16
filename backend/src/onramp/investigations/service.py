"""Running the investigator on a case and applying what it concluded.

The agent package only investigates; this service decides what happens to the case. A
validated resolution becomes a pending proposal from the agent, which a person accepts or
rejects. Anything else escalates, except a provider failure, which leaves the case to be
retried.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from opentelemetry import trace
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.agent.conclusion import to_proposal_request
from onramp.agent.graph import DEFAULT_BUDGET, Budget, InvestigationResult, investigate
from onramp.agent.model import ChatModel
from onramp.agent.models import InvestigationRun
from onramp.agent.policy_access import passage_lookup
from onramp.agent.prompts import DEFAULT_PROMPT_VERSION
from onramp.agent.tools import CaseScope, CaseToolbox
from onramp.audit.service import Actor, record_audit
from onramp.cases.escalation import EscalationReason
from onramp.cases.models import CaseType, SupportCase
from onramp.cases.queries import get_case
from onramp.cases.service import transition
from onramp.cases.state import CaseState
from onramp.checks.models import CheckRun
from onramp.db.types import new_id
from onramp.marketplace.reader import MarketplaceReader
from onramp.policy.search import PolicyRetriever
from onramp.proposals.service import InvalidProposalError, create_proposal
from onramp.telemetry.metrics import (
    investigation_duration,
    investigation_runs,
    investigation_tokens,
)

log = logging.getLogger(__name__)
_tracer = trace.get_tracer("onramp.investigations")
Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class NotInvestigatingError(Exception):
    pass


class InvestigationService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        marketplace: MarketplaceReader,
        retriever: PolicyRetriever,
        model: ChatModel,
        *,
        budget: Budget = DEFAULT_BUDGET,
        clock: Clock = _utcnow,
        prompt_version: str = DEFAULT_PROMPT_VERSION,
    ) -> None:
        self._sessions = sessions
        self._prompt_version = prompt_version
        self._marketplace = marketplace
        self._retriever = retriever
        self._model = model
        self._budget = budget
        self._clock = clock

    def run(self, tenant_id: str, case_id: str, actor: Actor) -> InvestigationRun:
        with _tracer.start_as_current_span(
            "investigation",
            attributes={
                "onramp.case_id": case_id,
                "onramp.tenant_id": tenant_id,
                "onramp.prompt_version": self._prompt_version,
            },
        ) as span:
            run = self._run(tenant_id, case_id, actor)
            attributes = {
                "status": run.status,
                "model": run.model_name,
                "prompt_version": run.prompt_version,
            }
            span.set_attributes(
                {
                    "onramp.investigation.status": run.status,
                    "onramp.model": run.model_name,
                    "onramp.model_calls": run.model_calls,
                    "onramp.tokens.input": run.input_tokens,
                    "onramp.tokens.output": run.output_tokens,
                }
            )
            investigation_runs.add(1, attributes)
            investigation_duration.record(run.elapsed_s, attributes)
            investigation_tokens.add(
                run.input_tokens, {"direction": "input", "model": run.model_name}
            )
            investigation_tokens.add(
                run.output_tokens, {"direction": "output", "model": run.model_name}
            )
            return run

    def _run(self, tenant_id: str, case_id: str, actor: Actor) -> InvestigationRun:
        with self._sessions() as session:
            case = get_case(session, tenant_id, case_id)
            if case.state != CaseState.INVESTIGATING:
                raise NotInvestigatingError(f"case is {case.state}")
            scope = case_scope(session, case)
            started_version = case.version

        # The model call happens outside any database transaction: it can take a minute.
        toolbox = CaseToolbox(
            scope,
            self._marketplace,
            self._retriever,
            passage_lookup(self._sessions, scope.case_type.value, scope.incident_at),
        )
        result = investigate(
            self._model,
            toolbox,
            scope.case_type,
            budget=self._budget,
            prompt_version=self._prompt_version,
        )

        now = self._clock()
        with self._sessions() as session, session.begin():
            case = get_case(session, tenant_id, case_id)
            run = _run_record(case, started_version, result)
            session.add(run)
            session.flush()
            if case.version != started_version or case.state != CaseState.INVESTIGATING:
                # Someone acted on the case while the model was working. Keep the record,
                # change nothing.
                run.status = "superseded"
                return run
            self._apply(session, case, run, result, actor, now)
            return run

    def _apply(
        self,
        session: Session,
        case: SupportCase,
        run: InvestigationRun,
        result: InvestigationResult,
        actor: Actor,
        now: datetime,
    ) -> None:
        details = {
            "investigation_run_id": run.id,
            "status": result.status,
            "model": result.model_name,
            "prompt_version": result.prompt_version,
        }
        record_audit(
            session,
            tenant_id=case.tenant_id,
            actor=Actor.agent(),
            subject_type="case",
            subject_id=case.id,
            action="investigation.completed",
            occurred_at=now,
            details=details,
        )
        if result.status == "model_error":
            return  # retryable; the case stays in INVESTIGATING
        conclusion = result.conclusion
        if result.status == "concluded" and conclusion is not None:
            if conclusion.decision == "resolve":
                try:
                    with session.begin_nested():
                        proposal = create_proposal(
                            session,
                            case,
                            to_proposal_request(conclusion),
                            proposer=Actor.agent(),
                            occurred_at=now,
                        )
                except (ValidationError, InvalidProposalError) as exc:
                    # Validation should have caught it; if it did not, the case must not stay
                    # stuck in investigation retrying the same answer. A person decides.
                    run.status = "invalid_output"
                    run.problems = [*run.problems, f"conclusion is not a valid proposal: {exc}"]
                    reason = EscalationReason.INVESTIGATION_FAILED
                else:
                    run.proposal_id = proposal.id
                    return
            else:
                reason = conclusion.escalation_reason or EscalationReason.OPERATOR_JUDGMENT
        else:
            reason = EscalationReason.INVESTIGATION_FAILED
        case.escalation_reason = reason.value
        transition(
            session,
            case,
            CaseState.ESCALATED,
            actor=Actor.agent(),
            occurred_at=now,
            reason=reason.value,
            details=details,
        )


def case_scope(session: Session, case: SupportCase) -> CaseScope:
    """The trusted facts an investigation of ``case`` is bound to."""
    latest_check = session.scalar(
        select(CheckRun)
        .filter_by(tenant_id=case.tenant_id, case_id=case.id)
        .order_by(CheckRun.created_at.desc(), CheckRun.id.desc())
        .limit(1)
    )
    return CaseScope(
        tenant_id=case.tenant_id,
        case_id=case.id,
        case_type=CaseType(case.case_type),
        subject_id=case.subject_id,
        worker_id=case.worker_id,
        incident_at=case.incident_at,
        reported_description=case.reported_description,
        check_findings=tuple(latest_check.findings) if latest_check else (),
    )


def _run_record(case: SupportCase, version: int, result: InvestigationResult) -> InvestigationRun:
    return InvestigationRun(
        id=new_id("inv"),
        tenant_id=case.tenant_id,
        case_id=case.id,
        case_version=version,
        model_name=result.model_name,
        prompt_version=result.prompt_version,
        status=result.status,
        conclusion=result.conclusion.model_dump(mode="json") if result.conclusion else None,
        problems=result.problems,
        model_calls=result.model_calls,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        elapsed_s=result.elapsed_s,
        trajectory=result.trajectory,
    )
