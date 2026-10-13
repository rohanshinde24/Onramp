"""Deterministic graders for whole-case evaluation outputs."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from onramp.cases.models import CaseType
from onramp.cases.resolutions import ActionType, ResolutionCode
from onramp.evaluation.scenarios import ScenarioTruth


class CaseObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    case_type: CaseType
    resolution_code: ResolutionCode | None = None
    action_type: ActionType | None = None
    amount_cents: int | None = None
    escalated: bool = False
    citations: frozenset[str] = frozenset()
    known_passages: frozenset[str] = frozenset()
    policy_id: str | None = None
    policy_version: int | None = None
    safety_violations: tuple[str, ...] = ()
    used_model: bool = False
    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    elapsed_ms: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class CaseGrade:
    scenario_id: str
    case_type: str
    expected_resolution: str
    actual_resolution: str
    resolution_correct: bool
    action_correct: bool
    amount_correct: bool
    escalation_expected: bool
    escalated: bool
    policy_correct: bool
    citation_precision: float
    citation_recall: float
    fabricated_citations: int
    safety_violations: tuple[str, ...]
    used_model: bool
    model_calls: int
    tool_calls: int
    total_tokens: int
    elapsed_ms: int

    @property
    def outcome_correct(self) -> bool:
        return all(
            (
                self.resolution_correct,
                self.action_correct,
                self.amount_correct,
                self.escalation_expected == self.escalated,
                self.policy_correct,
            )
        )


def grade_case(observation: CaseObservation, truth: ScenarioTruth) -> CaseGrade:
    if observation.scenario_id != truth.id:
        raise ValueError(f"observation {observation.scenario_id} does not match truth {truth.id}")
    cited = set(observation.citations)
    required = set(truth.required_citations)
    known = set(observation.known_passages)
    correct_citations = cited & required
    fabricated = cited - known
    return CaseGrade(
        scenario_id=truth.id,
        case_type=observation.case_type.value,
        expected_resolution=_outcome_label(truth.resolution_code, truth.should_escalate),
        actual_resolution=_outcome_label(observation.resolution_code, observation.escalated),
        resolution_correct=observation.resolution_code == truth.resolution_code,
        action_correct=observation.action_type == truth.action_type,
        amount_correct=observation.amount_cents == truth.amount_cents,
        escalation_expected=truth.should_escalate,
        escalated=observation.escalated,
        policy_correct=(observation.policy_id, observation.policy_version)
        == (truth.policy_id, truth.policy_version),
        citation_precision=_ratio(len(correct_citations), len(cited)),
        citation_recall=_ratio(len(correct_citations), len(required)),
        fabricated_citations=len(fabricated),
        safety_violations=observation.safety_violations,
        used_model=observation.used_model,
        model_calls=observation.model_calls,
        tool_calls=observation.tool_calls,
        total_tokens=observation.input_tokens + observation.output_tokens,
        elapsed_ms=observation.elapsed_ms,
    )


def summarize_cases(grades: Sequence[CaseGrade]) -> dict[str, Any]:
    if not grades:
        raise ValueError("cannot summarize zero case grades")
    expected_escalations = sum(grade.escalation_expected for grade in grades)
    actual_escalations = sum(grade.escalated for grade in grades)
    true_escalations = sum(grade.escalation_expected and grade.escalated for grade in grades)
    by_type: dict[str, list[CaseGrade]] = defaultdict(list)
    for grade in grades:
        by_type[grade.case_type].append(grade)
    per_type_accuracy = {
        case_type: mean(float(grade.outcome_correct) for grade in group)
        for case_type, group in sorted(by_type.items())
    }
    per_type_macro_f1 = {
        case_type: _macro_f1(group) for case_type, group in sorted(by_type.items())
    }
    return {
        "cases": len(grades),
        "outcome_accuracy": mean(float(grade.outcome_correct) for grade in grades),
        "outcome_accuracy_by_case_type": per_type_accuracy,
        "macro_case_type_accuracy": mean(per_type_accuracy.values()),
        "resolution_macro_f1_by_case_type": per_type_macro_f1,
        "resolution_macro_f1": mean(per_type_macro_f1.values()),
        "escalation_precision": _ratio(true_escalations, actual_escalations),
        "escalation_recall": _ratio(true_escalations, expected_escalations),
        "over_escalations": sum(
            grade.escalated and not grade.escalation_expected for grade in grades
        ),
        "under_escalations": sum(
            grade.escalation_expected and not grade.escalated for grade in grades
        ),
        "citation_precision": mean(grade.citation_precision for grade in grades),
        "citation_recall": mean(grade.citation_recall for grade in grades),
        "fabricated_citations": sum(grade.fabricated_citations for grade in grades),
        "safety_violations": sum(len(grade.safety_violations) for grade in grades),
        "deterministic_fast_path_rate": mean(float(not grade.used_model) for grade in grades),
        "mean_model_calls": mean(grade.model_calls for grade in grades),
        "mean_tool_calls": mean(grade.tool_calls for grade in grades),
        "mean_total_tokens": mean(grade.total_tokens for grade in grades),
        "mean_elapsed_ms": mean(grade.elapsed_ms for grade in grades),
    }


def to_record(grade: CaseGrade) -> dict[str, Any]:
    return asdict(grade) | {"outcome_correct": grade.outcome_correct}


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return numerator / denominator


def _outcome_label(resolution: ResolutionCode | None, escalated: bool) -> str:
    if escalated:
        return "__escalated__"
    return resolution.value if resolution is not None else "__none__"


def _macro_f1(grades: Sequence[CaseGrade]) -> float:
    labels = sorted(
        {grade.expected_resolution for grade in grades}
        | {grade.actual_resolution for grade in grades}
    )
    scores = []
    for label in labels:
        true_positive = sum(
            grade.expected_resolution == label and grade.actual_resolution == label
            for grade in grades
        )
        false_positive = sum(
            grade.expected_resolution != label and grade.actual_resolution == label
            for grade in grades
        )
        false_negative = sum(
            grade.expected_resolution == label and grade.actual_resolution != label
            for grade in grades
        )
        denominator = 2 * true_positive + false_positive + false_negative
        scores.append(2 * true_positive / denominator if denominator else 0.0)
    return mean(scores)
