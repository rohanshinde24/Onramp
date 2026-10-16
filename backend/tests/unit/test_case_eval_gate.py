from onramp.evaluation.report import gate_failures, limitations, summarize_runs
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


def test_live_model_runs_do_not_claim_scripted_investigations() -> None:
    scripted, live = limitations(live_model=False), limitations(live_model=True)

    assert scripted[0].startswith("Investigations are scripted")
    assert "scripted" not in live[0]
    assert "live model" in live[0]
    assert scripted[1:] == live[1:]
