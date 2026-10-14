"""Fault-injection scenarios: the application recovers to the normal outcome, writing once."""

from collections.abc import Callable

import pytest
from sqlalchemy import Engine, func, select

from onramp.agent.models import InvestigationRun
from onramp.audit.models import AuditEvent
from onramp.checks.models import CheckRun
from onramp.evaluation.case_grading import grade_case
from onramp.evaluation.faults import ScenarioFaults, load_faults
from onramp.evaluation.runner import RunStatus, ScenarioRun
from onramp.evaluation.safety import MUTATING_OPERATIONS
from onramp.evaluation.scenarios import ScenarioInput, ScenarioSet, TruthSet, load_scenarios
from onramp.evaluation.scripted import load_scripts
from onramp.marketplace.faults import Fault
from onramp.marketplace.models import WriteRequest
from tests.integration.eval_support import ROOT, Sessions, make_runner

SUITE = ROOT / "eval" / "faults"

# Scenario -> a step the fault and the recovery from it must leave behind.
EVIDENCE = {
    "lost_response_retried_by_gateway": None,  # retried inside the gateway, see write count
    "lost_responses_outlast_gateway_retries": "retry:execute_approved_action:OutcomeUnknownError",
    "activation_times_out_before_write": "retry:execute_approved_action:OutcomeUnknownError",
    "overturn_response_lost": "retry:execute_approved_action:OutcomeUnknownError",
    "evidence_request_response_lost": "retry:run_case_checks:OutcomeUnknownError",
    "provider_outage_then_recovers": "retry:run_investigation:InvestigationUnavailableError",
    "crash_after_checks_commit": "retry:run_case_checks:InjectedCrashError",
    "crash_after_investigation_commit": "retry:run_investigation:InjectedCrashError",
    "crash_after_execution_commit": "retry:execute_approved_action:InjectedCrashError",
    "crash_after_settlement_resume": "retry:resume_settlement_checks:InjectedCrashError",
    "crash_after_evidence_timeout": "retry:expire_evidence_wait:InjectedCrashError",
    "duplicate_settlement_event": "event:payment.settled:duplicate",
    "duplicate_evidence_upload": "event:evidence.uploaded:duplicate",
    "late_settlement_after_resolution": "event:payment.settled:applied",
}


@pytest.fixture(scope="module")
def suite() -> tuple[ScenarioSet, TruthSet]:
    return load_scenarios(SUITE / "scenarios.yaml", SUITE / "truth.yaml")


@pytest.fixture
def run_with_faults(
    onramp_engine: Engine,
    marketplace_engine: Engine,
    onramp_committed: Sessions,
    marketplace_committed: Sessions,
) -> Callable[..., ScenarioRun]:
    def run(
        scenario: ScenarioInput, faults: Callable[[str], ScenarioFaults] | None = None
    ) -> ScenarioRun:
        runner = make_runner(
            (onramp_engine, marketplace_engine),
            onramp_committed,
            marketplace_committed,
            load_scripts(SUITE / "investigations.yaml"),
            faults=faults or load_faults(SUITE / "faults.yaml"),
        )
        return runner.run(scenario)

    return run


def _mutations(marketplace: Sessions) -> int:
    with marketplace() as session:
        return (
            session.scalar(
                select(func.count())
                .select_from(WriteRequest)
                .where(WriteRequest.operation.in_(MUTATING_OPERATIONS))
            )
            or 0
        )


def test_every_fault_is_recovered_to_the_normal_outcome_with_one_write(
    suite: tuple[ScenarioSet, TruthSet],
    run_with_faults: Callable[..., ScenarioRun],
    onramp_committed: Sessions,
    marketplace_committed: Sessions,
) -> None:
    scenarios, truth = suite
    labels = {label.id: label for label in truth.truths}
    assert {s.id for s in scenarios.scenarios} == EVIDENCE.keys()

    for scenario in scenarios.scenarios:
        run = run_with_faults(scenario)

        assert run.status is RunStatus.SUCCESS, (scenario.id, run.error)
        assert run.observation is not None
        grade = grade_case(run.observation, labels[scenario.id])
        assert grade.outcome_correct, scenario.id
        assert grade.safety_violations == (), scenario.id
        if EVIDENCE[scenario.id] is not None:
            assert EVIDENCE[scenario.id] in run.steps, (scenario.id, run.steps)
        expected_writes = 1 if labels[scenario.id].action_type is not None else 0
        assert _mutations(marketplace_committed) == expected_writes, scenario.id
        with onramp_committed() as session:
            if scenario.id == "crash_after_checks_commit":
                assert session.scalar(select(func.count()).select_from(CheckRun)) == 1
            if scenario.id == "crash_after_investigation_commit":
                assert session.scalar(select(func.count()).select_from(InvestigationRun)) == 1
            if scenario.id == "late_settlement_after_resolution":
                assert run.final_state == "resolved"
                arrived = (
                    select(func.count())
                    .select_from(AuditEvent)
                    .filter_by(action="case.evidence_arrived")
                )
                assert session.scalar(arrived) == 0


def test_a_fault_that_outlasts_every_retry_is_reported_and_still_writes_at_most_once(
    suite: tuple[ScenarioSet, TruthSet],
    run_with_faults: Callable[..., ScenarioRun],
    marketplace_committed: Sessions,
) -> None:
    scenarios, _ = suite
    (scenario,) = [s for s in scenarios.scenarios if s.id == "lost_response_retried_by_gateway"]
    every_call = tuple(range(1, 31))
    # Every call loses its response: more than the gateway's and Temporal's retries combined.
    endless = ScenarioFaults(
        tuple(
            spec.model_copy(update={"calls": every_call, "fault": Fault.LOST_RESPONSE})
            for spec in load_faults(SUITE / "faults.yaml")(scenario.id).specs
        )
    )

    run = run_with_faults(scenario, faults=lambda _: endless)

    assert run.status is RunStatus.INJECTED_FAULT
    assert run.error is not None
    assert run.error.startswith("OutcomeUnknownError")
    assert _mutations(marketplace_committed) == 1
    assert run.observation is not None
    assert run.observation.safety_violations == ()
