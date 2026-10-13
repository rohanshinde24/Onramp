from onramp.evaluation.report import gate_failures, summarize_runs
from onramp.evaluation.runner import RunStatus, ScenarioRun


def test_failed_runs_fail_the_gate_even_without_an_accuracy_floor() -> None:
    summary = summarize_runs(
        [
            ScenarioRun("a", RunStatus.HARNESS_ERROR, error="setup"),
            ScenarioRun("b", RunStatus.APP_FAILURE, error="stalled"),
        ],
        [],
    )

    assert summary["graded"] is None
    assert gate_failures(summary, 0.0) == ["1 app_failure run(s)", "1 harness_error run(s)"]
    assert gate_failures(summary, 0.5)[-1] == "outcome accuracy 0.000 < 0.500"
