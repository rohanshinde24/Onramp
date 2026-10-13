"""Versioned case-evaluation scenarios with truth kept separate from visible inputs."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from onramp.cases.models import CaseType
from onramp.cases.resolutions import ActionType, ResolutionCode
from onramp.policy.corpus import PolicyDocument


class ScenarioInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    case_type: CaseType
    world_fixture: str = Field(min_length=1)
    # What the reporter names: a submission, worker or quality review in the world.
    subject_id: str = Field(min_length=1, max_length=64)
    report: str = Field(min_length=1, max_length=10_000)


class ScenarioSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    scenarios: tuple[ScenarioInput, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self) -> "ScenarioSet":
        _require_unique([scenario.id for scenario in self.scenarios], "scenario")
        return self


class ScenarioTruth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    resolution_code: ResolutionCode | None = None
    action_type: ActionType | None = None
    amount_cents: int | None = Field(default=None, gt=0)
    should_escalate: bool = False
    required_citations: frozenset[str] = frozenset()
    policy_id: str
    policy_version: int = Field(ge=1)
    # Why this is the right answer, for reviewers of the label. Never shown to the system.
    note: str = ""

    @model_validator(mode="after")
    def coherent_outcome(self) -> "ScenarioTruth":
        if self.should_escalate and (
            self.resolution_code is not None or self.action_type is not None
        ):
            raise ValueError("an escalation cannot also have a resolution or action")
        if self.action_type is ActionType.PAYOUT_ADJUSTMENT and self.amount_cents is None:
            raise ValueError("a payout adjustment requires amount_cents")
        if self.action_type is not ActionType.PAYOUT_ADJUSTMENT and self.amount_cents is not None:
            raise ValueError("amount_cents is only valid for payout adjustments")
        return self


class TruthSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    truths: tuple[ScenarioTruth, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self) -> "TruthSet":
        _require_unique([truth.id for truth in self.truths], "truth")
        return self


class ScenarioLabelsMismatchError(ValueError):
    pass


class TruthLabelError(ValueError):
    """A label cites a policy version or passage that does not exist."""


def load_scenarios(inputs_path: Path, truth_path: Path) -> tuple[ScenarioSet, TruthSet]:
    inputs = ScenarioSet.model_validate(yaml.safe_load(inputs_path.read_text()))
    truth = TruthSet.model_validate(yaml.safe_load(truth_path.read_text()))
    if inputs.version != truth.version:
        raise ScenarioLabelsMismatchError(
            f"input version {inputs.version} != truth version {truth.version}"
        )
    input_ids = {scenario.id for scenario in inputs.scenarios}
    truth_ids = {label.id for label in truth.truths}
    if input_ids != truth_ids:
        missing = sorted(input_ids - truth_ids)
        extra = sorted(truth_ids - input_ids)
        raise ScenarioLabelsMismatchError(f"missing truth={missing}; extra truth={extra}")
    return inputs, truth


def validate_truth(truth: TruthSet, corpus: list[PolicyDocument]) -> None:
    """Every labelled policy version must exist, and every required citation must be a
    passage of exactly that version."""
    passages = {
        (doc.front.policy_id, doc.front.version): {p.passage_id for p in doc.passages}
        for doc in corpus
    }
    for label in truth.truths:
        version = (label.policy_id, label.policy_version)
        if version not in passages:
            raise TruthLabelError(f"{label.id}: unknown policy {version}")
        foreign = sorted(label.required_citations - passages[version])
        if foreign:
            raise TruthLabelError(f"{label.id}: citations not in {version}: {foreign}")


def _require_unique(ids: list[str], kind: str) -> None:
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise ValueError(f"duplicate {kind} IDs: {duplicates}")
