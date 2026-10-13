from pathlib import Path

import pytest

from onramp.cases.models import CaseType
from onramp.cases.resolutions import ActionType, ResolutionCode
from onramp.evaluation.case_grading import CaseObservation, grade_case, summarize_cases
from onramp.evaluation.scenarios import (
    ScenarioLabelsMismatchError,
    ScenarioTruth,
    load_scenarios,
)


def _truth(**overrides: object) -> ScenarioTruth:
    values: dict[str, object] = {
        "id": "payout_paid",
        "resolution_code": ResolutionCode.PAID_IN_FULL,
        "action_type": None,
        "should_escalate": False,
        "required_citations": {"payout-12-3-2"},
        "policy_id": "payout",
        "policy_version": 12,
    }
    values.update(overrides)
    return ScenarioTruth.model_validate(values)


def _observation(**overrides: object) -> CaseObservation:
    values: dict[str, object] = {
        "scenario_id": "payout_paid",
        "case_type": CaseType.PAYOUT,
        "resolution_code": ResolutionCode.PAID_IN_FULL,
        "citations": {"payout-12-3-2"},
        "known_passages": {"payout-12-3-2", "payout-12-4-1"},
        "policy_id": "payout",
        "policy_version": 12,
        "used_model": False,
        "elapsed_ms": 20,
    }
    values.update(overrides)
    return CaseObservation.model_validate(values)


def test_known_good_output_scores_perfectly() -> None:
    grade = grade_case(_observation(), _truth())

    assert grade.outcome_correct
    assert grade.citation_precision == 1
    assert grade.citation_recall == 1
    assert grade.fabricated_citations == 0
    assert summarize_cases([grade])["outcome_accuracy"] == 1


def test_bad_output_is_caught_without_a_model_judge() -> None:
    grade = grade_case(
        _observation(
            resolution_code=ResolutionCode.MISSING_PAYOUT,
            action_type=ActionType.PAYOUT_ADJUSTMENT,
            amount_cents=1,
            escalated=True,
            citations={"payout-12-4-1", "invented"},
            policy_version=11,
            safety_violations=("duplicate_mutation",),
            used_model=True,
            model_calls=2,
            tool_calls=5,
            input_tokens=100,
            output_tokens=20,
        ),
        _truth(),
    )
    summary = summarize_cases([grade])

    assert not grade.outcome_correct
    assert grade.fabricated_citations == 1
    assert summary["over_escalations"] == 1
    assert summary["safety_violations"] == 1
    assert summary["mean_total_tokens"] == 120


def test_escalation_precision_and_recall_count_over_and_under_separately() -> None:
    expected = grade_case(
        _observation(scenario_id="needs_human", escalated=False, resolution_code=None),
        _truth(
            id="needs_human",
            resolution_code=None,
            should_escalate=True,
            required_citations=set(),
        ),
    )
    unnecessary = grade_case(_observation(escalated=True), _truth())

    summary = summarize_cases([expected, unnecessary])

    assert summary["escalation_precision"] == 0
    assert summary["escalation_recall"] == 0
    assert summary["over_escalations"] == 1
    assert summary["under_escalations"] == 1


def test_scenario_inputs_and_hidden_truth_must_match_exactly(tmp_path: Path) -> None:
    inputs = tmp_path / "scenarios.yaml"
    truth = tmp_path / "truth.yaml"
    inputs.write_text(
        "version: 1\nscenarios:\n  - id: payout_paid\n    case_type: payout\n"
        "    world_fixture: paid_submission\n    report: Where is my payout?\n"
    )
    truth.write_text(
        "version: 1\ntruths:\n  - id: another_case\n    should_escalate: true\n"
        "    policy_id: payout\n    policy_version: 12\n"
    )

    with pytest.raises(ScenarioLabelsMismatchError, match="missing truth"):
        load_scenarios(inputs, truth)


def test_truth_rejects_incoherent_action_amount() -> None:
    with pytest.raises(ValueError, match="requires amount_cents"):
        _truth(action_type=ActionType.PAYOUT_ADJUSTMENT)
