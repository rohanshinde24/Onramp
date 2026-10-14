from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from onramp.evaluation.faults import FaultSet, Retry, ScenarioFaults, load_faults
from onramp.workflows.case import ACTIVITY_RETRY, INVESTIGATION_RETRY

ROOT = Path(__file__).resolve().parents[3]


def test_retries_mirror_the_workflow_policies() -> None:
    activity = Retry.mirroring(ACTIVITY_RETRY, unlimited_cap=5)
    investigation = Retry.mirroring(INVESTIGATION_RETRY, unlimited_cap=5)

    assert activity.attempts == 5
    assert [activity.delay(n) for n in (1, 2, 3)] == [timedelta(seconds=s) for s in (1, 2, 4)]
    assert investigation.delay(1) == timedelta(seconds=30)
    assert investigation.delay(10) == timedelta(minutes=10)


def test_crashes_are_used_up_as_they_fire() -> None:
    faults = ScenarioFaults(
        FaultSet.model_validate(
            {
                "version": 1,
                "faults": {
                    "s": [{"kind": "crash_after", "activity": "run_case_checks", "times": 2}]
                },
            }
        ).faults["s"]
    )

    fired = [faults.crash_after("run_case_checks") for _ in range(3)]

    assert fired == [True, True, False]
    assert not faults.crash_after("run_investigation")
    assert faults.labels == ["crash_after:run_case_checksx2"]


def test_committed_fault_suite_is_valid_and_unknown_kinds_are_rejected() -> None:
    faults = load_faults(ROOT / "eval" / "faults" / "faults.yaml")

    assert faults("lost_responses_outlast_gateway_retries").marketplace_plan() is not None
    assert faults("not_listed").labels == []
    with pytest.raises(ValidationError):
        FaultSet.model_validate({"version": 1, "faults": {"s": [{"kind": "meteor"}]}})
