from collections import deque
from itertools import product

import pytest

from onramp.cases.state import (
    TERMINAL_STATES,
    TRANSITIONS,
    CaseState,
    IllegalTransitionError,
    can_transition,
    is_terminal,
    require_transition,
)

ALLOWED = {(a, b) for a, targets in TRANSITIONS.items() for b in targets}


def _reachable(start: CaseState) -> set[CaseState]:
    seen, queue = {start}, deque([start])
    while queue:
        for nxt in TRANSITIONS[queue.popleft()]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def test_every_state_has_a_transition_entry() -> None:
    assert set(TRANSITIONS) == set(CaseState)


@pytest.mark.parametrize(("current", "target"), sorted(ALLOWED))
def test_allowed_transitions_pass(current: CaseState, target: CaseState) -> None:
    require_transition(current, target)


def test_every_unlisted_transition_is_rejected() -> None:
    for current, target in product(CaseState, CaseState):
        if (current, target) in ALLOWED:
            continue
        assert not can_transition(current, target)
        with pytest.raises(IllegalTransitionError):
            require_transition(current, target)


def test_terminal_states_have_no_exits() -> None:
    for state in TERMINAL_STATES:
        assert is_terminal(state)
        assert TRANSITIONS[state] == frozenset()


def test_every_state_is_reachable_from_created() -> None:
    assert _reachable(CaseState.CREATED) == set(CaseState)


def test_every_non_terminal_state_can_escalate_or_is_escalated() -> None:
    for state in set(CaseState) - TERMINAL_STATES - {CaseState.ESCALATED}:
        assert can_transition(state, CaseState.ESCALATED), state


def test_no_non_terminal_state_is_a_dead_end() -> None:
    for state in set(CaseState) - TERMINAL_STATES:
        assert _reachable(state) & TERMINAL_STATES, state


def test_execution_cannot_skip_verification() -> None:
    assert not can_transition(CaseState.EXECUTING, CaseState.RESOLVED)


def test_approval_cannot_be_skipped_once_requested() -> None:
    assert not can_transition(CaseState.AWAITING_APPROVAL, CaseState.RESOLVED)
    assert not can_transition(CaseState.AWAITING_APPROVAL, CaseState.VERIFYING)
