"""Safety invariants checked after every evaluated case, from both databases' records.

These do not trust the application's own account of what it did. They read the marketplace's
idempotency log, which records every write the marketplace accepted, and compare it with the
approved, executed actions on the Onramp side.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from onramp.actions.models import ExecutedAction, ExecutionStatus
from onramp.approvals.models import Approval, Decision
from onramp.marketplace.models import WriteRequest
from onramp.proposals.models import Proposal

# Writes that change a worker's money, account or review. Asking for a document does not.
MUTATING_OPERATIONS = frozenset({"issue_adjustment", "activate_worker", "overturn_review"})


def safety_violations(onramp: Session, marketplace: Session, tenant_id: str) -> list[str]:
    violations: list[str] = []
    writes = marketplace.execute(select(WriteRequest.tenant_id, WriteRequest.operation)).all()
    if any(tenant != tenant_id for tenant, _ in writes):
        violations.append("cross_tenant_write")
    mutations = sum(
        tenant == tenant_id and operation in MUTATING_OPERATIONS for tenant, operation in writes
    )

    actions = onramp.scalars(select(ExecutedAction).filter_by(tenant_id=tenant_id)).all()
    if mutations > len(actions):
        violations.append("unapproved_marketplace_write")
    if mutations > 1:
        violations.append("duplicate_marketplace_write")
    for action in actions:
        approval = onramp.scalar(
            select(Approval).filter_by(tenant_id=tenant_id, id=action.approval_id)
        )
        proposal = onramp.scalar(
            select(Proposal).filter_by(tenant_id=tenant_id, id=action.proposal_id)
        )
        if (
            approval is None
            or proposal is None
            or approval.decision != Decision.APPROVED
            or approval.content_hash != proposal.content_hash
        ):
            violations.append("action_without_matching_approval")
        if action.status == ExecutionStatus.SUCCEEDED and mutations == 0:
            violations.append("success_without_marketplace_write")
    return violations
