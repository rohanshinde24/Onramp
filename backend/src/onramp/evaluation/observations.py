"""Normalize authoritative stored case records into deterministic evaluation observations."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from onramp.actions.models import ExecutedAction
from onramp.agent.models import InvestigationRun
from onramp.cases.models import CaseType, SupportCase
from onramp.cases.queries import get_case
from onramp.cases.resolutions import ActionType, ResolutionCode
from onramp.cases.state import CaseState
from onramp.checks.models import CheckRun
from onramp.evaluation.case_grading import CaseObservation
from onramp.policy.models import PolicyPassage
from onramp.proposals.models import Proposal


def observe_case(
    session: Session,
    tenant_id: str,
    case_id: str,
    scenario_id: str,
    *,
    safety_violations: Sequence[str] = (),
) -> CaseObservation:
    case = get_case(session, tenant_id, case_id)
    check = session.scalar(
        select(CheckRun)
        .filter_by(tenant_id=tenant_id, case_id=case_id)
        .order_by(CheckRun.created_at.desc(), CheckRun.id.desc())
        .limit(1)
    )
    investigation = session.scalar(
        select(InvestigationRun)
        .filter_by(tenant_id=tenant_id, case_id=case_id)
        .order_by(InvestigationRun.created_at.desc(), InvestigationRun.id.desc())
        .limit(1)
    )
    proposal = session.scalar(
        select(Proposal)
        .filter_by(tenant_id=tenant_id, case_id=case_id)
        .order_by(Proposal.created_at.desc(), Proposal.id.desc())
        .limit(1)
    )
    action = session.scalar(
        select(ExecutedAction)
        .filter_by(tenant_id=tenant_id, case_id=case_id)
        .order_by(ExecutedAction.created_at.desc(), ExecutedAction.id.desc())
        .limit(1)
    )
    citations = set(check.citations if check else ())
    if proposal is not None:
        citations.update(
            ref.removeprefix("policy_passage:")
            for ref in proposal.evidence_refs
            if ref.startswith("policy_passage:")
        )
    known_passages = frozenset(session.scalars(select(PolicyPassage.passage_id)))
    amount = _amount(action.request if action else proposal.action_params if proposal else {})
    return CaseObservation(
        scenario_id=scenario_id,
        case_type=CaseType(case.case_type),
        resolution_code=_resolution(case, proposal),
        action_type=ActionType(proposal.action_type) if proposal and proposal.action_type else None,
        amount_cents=amount,
        escalated=case.state == CaseState.ESCALATED,
        citations=frozenset(citations),
        known_passages=known_passages,
        policy_id=check.policy_id if check else None,
        policy_version=check.policy_version if check else None,
        safety_violations=tuple(safety_violations),
        used_model=investigation is not None,
        model_calls=investigation.model_calls if investigation else 0,
        tool_calls=(
            sum(entry.get("kind") == "tool" for entry in investigation.trajectory)
            if investigation
            else 0
        ),
        input_tokens=investigation.input_tokens if investigation else 0,
        output_tokens=investigation.output_tokens if investigation else 0,
        elapsed_ms=round(investigation.elapsed_s * 1000) if investigation else 0,
    )


def _resolution(case: SupportCase, proposal: Proposal | None) -> ResolutionCode | None:
    value = case.resolution_code or (proposal.resolution_code if proposal else None)
    return ResolutionCode(value) if value else None


def _amount(params: dict[str, object]) -> int | None:
    amount = params.get("amount_cents")
    return amount if isinstance(amount, int) else None
