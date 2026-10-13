"""Support case lifecycle (PRODUCT_SPEC section 4).

The transition table is the single definition of which state changes are legal. Every state
change in the application goes through ``require_transition``; an illegal one is an error,
never a silent no-op.
"""

from enum import StrEnum


class CaseState(StrEnum):
    CREATED = "created"
    INITIAL_CHECKS = "initial_checks"
    INVESTIGATING = "investigating"
    WAITING_FOR_EVIDENCE = "waiting_for_evidence"
    WAITING_FOR_SETTLEMENT = "waiting_for_settlement"
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    CLOSED_NO_ACTION = "closed_no_action"


S = CaseState

TERMINAL_STATES = frozenset({S.RESOLVED, S.CLOSED_NO_ACTION})

TRANSITIONS: dict[CaseState, frozenset[CaseState]] = {
    S.CREATED: frozenset({S.INITIAL_CHECKS, S.ESCALATED}),
    # Checks may settle the case directly (PROPOSED), need the agent (INVESTIGATING),
    # need a document (WAITING_FOR_EVIDENCE), or need the settlement window to pass.
    S.INITIAL_CHECKS: frozenset(
        {S.INVESTIGATING, S.PROPOSED, S.WAITING_FOR_EVIDENCE, S.WAITING_FOR_SETTLEMENT, S.ESCALATED}
    ),
    S.INVESTIGATING: frozenset({S.PROPOSED, S.WAITING_FOR_EVIDENCE, S.ESCALATED}),
    # Evidence arrived, or the wait timed out.
    S.WAITING_FOR_EVIDENCE: frozenset({S.INVESTIGATING, S.ESCALATED}),
    # The window closed: re-run checks against current payment state.
    S.WAITING_FOR_SETTLEMENT: frozenset({S.INITIAL_CHECKS, S.ESCALATED}),
    # Accepted without an action, sent for approval, executed directly (no approval needed),
    # rejected back to investigation, or escalated.
    S.PROPOSED: frozenset(
        {S.RESOLVED, S.AWAITING_APPROVAL, S.EXECUTING, S.INVESTIGATING, S.ESCALATED}
    ),
    S.AWAITING_APPROVAL: frozenset({S.EXECUTING, S.INVESTIGATING, S.ESCALATED}),
    S.EXECUTING: frozenset({S.VERIFYING, S.ESCALATED}),
    S.VERIFYING: frozenset({S.RESOLVED, S.ESCALATED}),
    # A lead works the case: proposes, sends it back for investigation, resolves without an
    # action, or closes it (duplicate, withdrawn).
    S.ESCALATED: frozenset({S.PROPOSED, S.INVESTIGATING, S.RESOLVED, S.CLOSED_NO_ACTION}),
    S.RESOLVED: frozenset(),
    S.CLOSED_NO_ACTION: frozenset(),
}


class IllegalTransitionError(Exception):
    def __init__(self, current: CaseState, target: CaseState) -> None:
        super().__init__(f"illegal case transition {current.value} -> {target.value}")
        self.current = current
        self.target = target


def can_transition(current: CaseState, target: CaseState) -> bool:
    return target in TRANSITIONS[current]


def require_transition(current: CaseState, target: CaseState) -> None:
    if not can_transition(current, target):
        raise IllegalTransitionError(current, target)


def is_terminal(state: CaseState) -> bool:
    return state in TERMINAL_STATES
