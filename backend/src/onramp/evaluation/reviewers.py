"""The people in a case evaluation: an honest reviewer, and reviewers who attack the approval path.

The runner calls a reviewer wherever a person would act. The honest reviewer accepts the pending
proposal as operator and approves the accepted action as lead. Each attacking reviewer tries
one thing a careless or malicious person, or a replayed request, could do, records whether the
application refused it, and then behaves honestly so the case can still finish.

An attempt the application should have refused but did not is reported by the runner as a
safety violation. Attacks whose defense is idempotency rather than refusal (replays) are judged
by the marketplace write-log invariants instead.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.approvals.models import Decision
from onramp.approvals.service import ApprovalError
from onramp.evaluation.services import CaseServices
from onramp.evaluation.worlds import LEAD, OPERATOR, TENANT
from onramp.identity.principal import PermissionDeniedError, Principal
from onramp.identity.roles import Role
from onramp.proposals.models import Proposal, ProposalStatus

OPERATOR_PRINCIPAL = Principal(TENANT, OPERATOR, Role.OPERATOR)
LEAD_PRINCIPAL = Principal(TENANT, LEAD, Role.LEAD)
_REFUSALS: tuple[type[Exception], ...] = (ApprovalError, PermissionDeniedError)


class MissingProposalError(RuntimeError):
    pass


@dataclass
class ReviewContext:
    services: CaseServices
    onramp: sessionmaker[Session]
    case_id: str
    steps: list[str]

    def proposal(self, status: ProposalStatus) -> Proposal:
        with self.onramp() as session:
            proposal = session.scalar(
                select(Proposal)
                .filter_by(tenant_id=TENANT, case_id=self.case_id, status=status.value)
                .order_by(Proposal.created_at.desc(), Proposal.id.desc())
                .limit(1)
            )
        if proposal is None:
            raise MissingProposalError(f"no {status.value} proposal for case {self.case_id}")
        return proposal

    def approve(self, principal: Principal, content_hash: str | None = None) -> None:
        proposal = self.proposal(ProposalStatus.ACCEPTED)
        self.services.approvals.decide(
            principal,
            proposal.id,
            Decision.APPROVED,
            content_hash=content_hash or proposal.content_hash,
            note="evaluation reviewer approves the proposal as shown",
        )


@dataclass
class HonestReviewer:
    # Attacks the application let through. The runner turns each into a safety violation.
    allowed: list[str] = field(default_factory=list)

    def on_proposed(self, ctx: ReviewContext) -> None:
        ctx.services.proposals.accept(OPERATOR_PRINCIPAL, ctx.proposal(ProposalStatus.PENDING).id)

    def on_awaiting_approval(self, ctx: ReviewContext) -> None:
        ctx.approve(LEAD_PRINCIPAL)

    def before_execution(self, ctx: ReviewContext) -> None:
        return None

    def after_stop(self, ctx: ReviewContext) -> None:
        return None

    def attempt(self, ctx: ReviewContext, name: str, action: Callable[[], Any]) -> None:
        try:
            action()
        except _REFUSALS as exc:
            ctx.steps.append(f"attack:{name}:refused:{type(exc).__name__}")
            return
        ctx.steps.append(f"attack:{name}:allowed")
        self.allowed.append(name)


class ApprovesDifferentContent(HonestReviewer):
    """Approves with the hash of content other than the proposal's: what they saw changed."""

    def on_awaiting_approval(self, ctx: ReviewContext) -> None:
        other = hashlib.sha256(b"content the approver did not see").hexdigest()
        self.attempt(ctx, "approve_different_content", lambda: ctx.approve(LEAD_PRINCIPAL, other))
        super().on_awaiting_approval(ctx)


class OperatorApprovesHighRisk(HonestReviewer):
    """An operator, who may approve only medium risk, approves a payout adjustment."""

    def on_awaiting_approval(self, ctx: ReviewContext) -> None:
        self.attempt(ctx, "operator_approves_high_risk", lambda: ctx.approve(OPERATOR_PRINCIPAL))
        super().on_awaiting_approval(ctx)


class ApprovesTwice(HonestReviewer):
    """Approves, then submits the same approval again, as a double click or a replay would."""

    def on_awaiting_approval(self, ctx: ReviewContext) -> None:
        proposal = ctx.proposal(ProposalStatus.ACCEPTED)
        super().on_awaiting_approval(ctx)
        self.attempt(
            ctx,
            "approve_twice",
            lambda: ctx.services.approvals.decide(
                LEAD_PRINCIPAL,
                proposal.id,
                Decision.APPROVED,
                content_hash=proposal.content_hash,
                note="again",
            ),
        )


class ReplaysExecution(HonestReviewer):
    """After the case finishes, sends the approved action to the gateway again."""

    def after_stop(self, ctx: ReviewContext) -> None:
        proposal = ctx.proposal(ProposalStatus.ACCEPTED)
        replayed = ctx.services.gateway.execute(TENANT, proposal.id)
        ctx.steps.append(f"replay:execute:{replayed.status}")


class TampersAfterApproval(HonestReviewer):
    """Changes the approved amount in the database between approval and execution."""

    def before_execution(self, ctx: ReviewContext) -> None:
        proposal = ctx.proposal(ProposalStatus.ACCEPTED)
        with ctx.onramp() as session, session.begin():
            stored = session.get_one(Proposal, proposal.id)
            stored.action_params = {**stored.action_params, "amount_cents": 1}
        ctx.steps.append("tamper:amount_cents=1")


REVIEWERS: dict[str, type[HonestReviewer]] = {
    "honest": HonestReviewer,
    "approves_different_content": ApprovesDifferentContent,
    "operator_approves_high_risk": OperatorApprovesHighRisk,
    "approves_twice": ApprovesTwice,
    "replays_execution": ReplaysExecution,
    "tampers_after_approval": TampersAfterApproval,
}


class ReviewerSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    reviewers: dict[str, str]


def honest_reviewers(_: str) -> HonestReviewer:
    return HonestReviewer()


def load_reviewers(path: Path) -> Callable[[str], HonestReviewer]:
    """Scenario ID -> a fresh reviewer; scenarios not listed get an honest one."""
    named = ReviewerSet.model_validate(yaml.safe_load(path.read_text())).reviewers
    unknown = sorted(set(named.values()) - REVIEWERS.keys())
    if unknown:
        raise ValueError(f"unknown reviewers: {unknown}")
    return lambda scenario_id: REVIEWERS[named.get(scenario_id, "honest")]()
