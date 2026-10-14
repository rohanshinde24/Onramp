"""What the investigator may return, and the deterministic validation every answer passes.

The schema has no field for tenant, worker, account or payee: those come from the case. The
validation checks every reference against what the investigation actually observed, so a
citation the agent did not retrieve, or a record it did not read, is rejected without asking
any model whether it "looks grounded".
"""

from dataclasses import dataclass, field
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from onramp.cases.escalation import EscalationReason
from onramp.cases.models import CaseType
from onramp.cases.resolutions import RESOLUTIONS, ActionType, ResolutionCode
from onramp.proposals.content import REASON_CODE_PATTERN, ProposalRequest


class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_type: ActionType
    amount_cents: int | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class AgentConclusion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["resolve", "escalate"]
    resolution_code: ResolutionCode | None = None
    escalation_reason: EscalationReason | None = None
    confidence: float = Field(ge=0, le=1)
    # The same format a proposal requires, stated in the schema the model sees, so a malformed
    # code is a correctable validation problem rather than a failure after the conclusion.
    reason_codes: list[Annotated[str, StringConstraints(pattern=REASON_CODE_PATTERN)]] = Field(
        default_factory=list, max_length=10
    )
    citations: list[str] = Field(default_factory=list, max_length=20)
    evidence_refs: list[str] = Field(default_factory=list, max_length=50)
    proposed_action: ProposedAction | None = None
    rationale: str = Field(min_length=1, max_length=4000)


@dataclass
class Observations:
    """What tools actually returned during this investigation."""

    record_refs: set[str] = field(default_factory=set)
    passage_ids: set[str] = field(default_factory=set)


def validate_conclusion(
    conclusion: AgentConclusion, case_type: CaseType, observed: Observations
) -> list[str]:
    """Return every problem found; an empty list means the conclusion may become a proposal."""
    problems: list[str] = []

    forged_citations = sorted(set(conclusion.citations) - observed.passage_ids)
    if forged_citations:
        problems.append(f"citations not retrieved in this investigation: {forged_citations}")
    unseen_refs = sorted(set(conclusion.evidence_refs) - observed.record_refs)
    if unseen_refs:
        problems.append(f"evidence references not read in this investigation: {unseen_refs}")

    if conclusion.decision == "escalate":
        if conclusion.escalation_reason is None:
            problems.append("escalation requires an escalation_reason")
        if conclusion.proposed_action is not None:
            problems.append("an escalation cannot propose an action")
        return problems

    if conclusion.resolution_code is None:
        problems.append("a resolution requires a resolution_code")
        return problems
    allowed = RESOLUTIONS[case_type]
    if conclusion.resolution_code not in allowed:
        problems.append(f"{conclusion.resolution_code} is not a {case_type} resolution")
        return problems
    if not conclusion.citations:
        problems.append("a resolution must cite at least one policy passage")
    if not conclusion.evidence_refs:
        problems.append("a resolution must reference at least one marketplace record")

    required = allowed[conclusion.resolution_code]
    proposed = conclusion.proposed_action
    if required is None and proposed is not None:
        problems.append(f"{conclusion.resolution_code} must not carry an action")
    if required is not None:
        if proposed is None or proposed.action_type is not required:
            problems.append(f"{conclusion.resolution_code} requires action {required}")
        elif required is ActionType.PAYOUT_ADJUSTMENT and (
            proposed.amount_cents is None or proposed.currency is None
        ):
            problems.append("a payout adjustment needs amount_cents and currency")
    return problems


def to_proposal_request(conclusion: AgentConclusion) -> ProposalRequest:
    """Only for a conclusion that passed validation with decision 'resolve'."""
    if conclusion.decision != "resolve" or conclusion.resolution_code is None:
        raise ValueError("only a validated resolution becomes a proposal")
    action = conclusion.proposed_action
    params: dict[str, object] = {}
    if action is not None and action.action_type is ActionType.PAYOUT_ADJUSTMENT:
        params = {"amount_cents": action.amount_cents, "currency": action.currency}
    return ProposalRequest(
        resolution_code=conclusion.resolution_code,
        action_type=action.action_type if action else None,
        action_params=params,
        reason_codes=conclusion.reason_codes,
        evidence_refs=[
            *conclusion.evidence_refs,
            *(f"policy_passage:{c}" for c in conclusion.citations),
        ],
        rationale=conclusion.rationale,
    )
