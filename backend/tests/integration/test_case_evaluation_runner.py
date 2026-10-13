"""The case-evaluation runner over the real services, with known-good and seeded-bad inputs."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from onramp.agent.model import ChatModel
from onramp.entrypoints.case_eval import build_services
from onramp.evaluation.case_grading import CaseGrade, grade_case
from onramp.evaluation.report import gate_failures, run_cases
from onramp.evaluation.runner import (
    CaseEvaluationRunner,
    CaseServices,
    RunStatus,
    ScenarioRun,
    VirtualClock,
)
from onramp.evaluation.scenarios import ScenarioInput, ScenarioSet, TruthSet, load_scenarios
from onramp.evaluation.scripted import KeywordPolicyRetriever, ScriptSet, load_scripts
from onramp.evaluation.worlds import reset_databases
from onramp.marketplace.records import PaymentRecord
from onramp.marketplace.writer import MarketplaceWriter, SqlMarketplaceWriter
from onramp.policy.corpus import load_corpus

ROOT = Path(__file__).resolve().parents[3]
CASES = ROOT / "eval" / "cases"
CORPUS = load_corpus(ROOT / "policies")
Sessions = sessionmaker[Session]
WriterFactory = Callable[[Sessions, VirtualClock], MarketplaceWriter]


@pytest.fixture(scope="module")
def dataset() -> tuple[ScenarioSet, TruthSet]:
    return load_scenarios(CASES / "scenarios.yaml", CASES / "truth.yaml")


def _runner(
    engines: tuple[Engine, Engine],
    onramp: Sessions,
    marketplace: Sessions,
    scripts: ScriptSet,
    writer: WriterFactory | None = None,
) -> CaseEvaluationRunner:
    def services(clock: VirtualClock, model: ChatModel) -> CaseServices:
        return build_services(
            onramp,
            marketplace,
            KeywordPolicyRetriever(onramp),
            clock,
            model,
            writer(marketplace, clock) if writer else None,
        )

    return CaseEvaluationRunner(
        onramp,
        marketplace,
        lambda: reset_databases(*engines),
        services,
        CORPUS,
        scripts.model_for,
    )


@pytest.fixture
def run_scenario(
    onramp_engine: Engine,
    marketplace_engine: Engine,
    onramp_committed: Sessions,
    marketplace_committed: Sessions,
) -> Callable[..., ScenarioRun]:
    def run(
        scenario: ScenarioInput,
        scripts: ScriptSet | None = None,
        writer: WriterFactory | None = None,
    ) -> ScenarioRun:
        runner = _runner(
            (onramp_engine, marketplace_engine),
            onramp_committed,
            marketplace_committed,
            scripts or load_scripts(CASES / "investigations.yaml"),
            writer,
        )
        return runner.run(scenario)

    return run


def _grade(run: ScenarioRun, truth: TruthSet) -> CaseGrade:
    assert run.observation is not None, run.error
    (label,) = [t for t in truth.truths if t.id == run.scenario_id]
    return grade_case(run.observation, label)


def _scenario(dataset: tuple[ScenarioSet, TruthSet], scenario_id: str) -> ScenarioInput:
    (scenario,) = [s for s in dataset[0].scenarios if s.id == scenario_id]
    return scenario


def _with_submit(scenario_id: str, **overrides: Any) -> ScriptSet:
    """The reference scripts with one scenario's conclusion changed."""
    raw = load_scripts(CASES / "investigations.yaml").model_dump()
    turns = raw["investigations"][scenario_id]
    turns[-1][0]["args"].update(overrides)
    return ScriptSet.model_validate(raw)


def test_reference_run_gets_every_committed_scenario_right(
    dataset: tuple[ScenarioSet, TruthSet], run_scenario: Callable[..., ScenarioRun]
) -> None:
    scenarios, truth = dataset
    grades = []
    for scenario in scenarios.scenarios:
        run = run_scenario(scenario)
        assert run.status is RunStatus.SUCCESS, (scenario.id, run.error, run.steps)
        grades.append(_grade(run, truth))

    wrong = [
        (g.scenario_id, g.expected_resolution, g.actual_resolution)
        for g in grades
        if not g.outcome_correct
    ]
    assert wrong == []
    assert [g.scenario_id for g in grades if g.safety_violations] == []
    assert [g.scenario_id for g in grades if g.citation_recall < 1] == []
    assert sum(g.fabricated_citations for g in grades) == 0
    assert {g.scenario_id for g in grades if g.used_model} == {
        "payout_partial_settlement_v11",
        "onboarding_unverified_upload",
    }


def test_settlement_wait_resumes_at_its_deadline(
    dataset: tuple[ScenarioSet, TruthSet], run_scenario: Callable[..., ScenarioRun]
) -> None:
    run = run_scenario(_scenario(dataset, "payout_within_window"))

    assert "waiting_for_settlement" in run.steps
    assert run.final_state == "resolved"


def test_wrong_agent_amount_is_refused_by_the_gateway_and_graded_wrong(
    dataset: tuple[ScenarioSet, TruthSet], run_scenario: Callable[..., ScenarioRun]
) -> None:
    scripts = _with_submit(
        "payout_partial_settlement_v11",
        proposed_action={
            "action_type": "PAYOUT_ADJUSTMENT",
            "amount_cents": 6000,
            "currency": "USD",
        },
    )

    run = run_scenario(_scenario(dataset, "payout_partial_settlement_v11"), scripts)
    grade = _grade(run, dataset[1])

    assert run.status is RunStatus.SUCCESS
    assert any("payout_recomputation_mismatch" in step for step in run.steps)
    assert run.final_state == "escalated"
    assert not grade.outcome_correct
    assert grade.safety_violations == ()


def test_wrong_agent_resolution_is_graded_wrong(
    dataset: tuple[ScenarioSet, TruthSet], run_scenario: Callable[..., ScenarioRun]
) -> None:
    scripts = _with_submit(
        "payout_partial_settlement_v11",
        resolution_code="PAID_IN_FULL",
        proposed_action=None,
    )

    run = run_scenario(_scenario(dataset, "payout_partial_settlement_v11"), scripts)
    grade = _grade(run, dataset[1])

    assert run.final_state == "resolved"
    assert not grade.resolution_correct
    assert not grade.amount_correct


def test_forged_citation_never_reaches_a_proposal(
    dataset: tuple[ScenarioSet, TruthSet], run_scenario: Callable[..., ScenarioRun]
) -> None:
    raw = load_scripts(CASES / "investigations.yaml").model_dump()
    turns = list(raw["investigations"]["payout_partial_settlement_v11"])
    forged = {
        **turns[-1][0],
        "args": {**turns[-1][0]["args"], "citations": ["payout_v12_section_4_4"]},
    }
    turns[-1] = [forged]
    turns.extend([[forged], [forged]])
    raw["investigations"]["payout_partial_settlement_v11"] = turns

    run = run_scenario(
        _scenario(dataset, "payout_partial_settlement_v11"), ScriptSet.model_validate(raw)
    )
    grade = _grade(run, dataset[1])

    assert run.final_state == "escalated"
    assert not grade.outcome_correct
    assert run.observation is not None
    assert "payout_v12_section_4_4" not in run.observation.citations


class _DoublePayingWriter(SqlMarketplaceWriter):
    """A sabotaged writer that issues every adjustment twice under different keys."""

    def issue_adjustment(
        self,
        tenant_id: str,
        worker_id: str,
        amount_cents: int,
        currency: str,
        *,
        idempotency_key: str,
    ) -> PaymentRecord:
        args = (tenant_id, worker_id, amount_cents, currency)
        super().issue_adjustment(*args, idempotency_key=f"{idempotency_key}:again")
        return super().issue_adjustment(*args, idempotency_key=idempotency_key)


def test_duplicate_marketplace_write_is_a_safety_violation(
    dataset: tuple[ScenarioSet, TruthSet], run_scenario: Callable[..., ScenarioRun]
) -> None:
    run = run_scenario(
        _scenario(dataset, "payout_settlement_failed"),
        writer=lambda sessions, clock: _DoublePayingWriter(sessions, clock),
    )
    grade = _grade(run, dataset[1])

    assert set(grade.safety_violations) >= {
        "duplicate_marketplace_write",
        "unapproved_marketplace_write",
    }


def test_an_investigation_without_a_script_is_an_app_failure(
    dataset: tuple[ScenarioSet, TruthSet], run_scenario: Callable[..., ScenarioRun]
) -> None:
    empty = ScriptSet(version=1, model="none", investigations={})

    run = run_scenario(_scenario(dataset, "onboarding_unverified_upload"), empty)

    assert run.status is RunStatus.APP_FAILURE
    assert run.error is not None
    assert "InvestigationUnavailableError" in run.error
    assert run.observation is not None


def test_an_unknown_world_is_a_harness_error(run_scenario: Callable[..., ScenarioRun]) -> None:
    scenario = ScenarioInput(
        id="missing_world",
        case_type="payout",
        world_fixture="no_such_world",
        subject_id="s_1",
        report="x",
    )

    run = run_scenario(scenario)

    assert run.status is RunStatus.HARNESS_ERROR
    assert run.observation is None


def test_run_writes_manifest_records_and_summary_and_gates_a_bad_run(
    dataset: tuple[ScenarioSet, TruthSet],
    onramp_engine: Engine,
    marketplace_engine: Engine,
    onramp_committed: Sessions,
    marketplace_committed: Sessions,
    tmp_path: Path,
) -> None:
    scenarios, truth = dataset
    subset = ScenarioSet(
        version=scenarios.version,
        scenarios=(
            _scenario(dataset, "payout_paid_in_full"),
            _scenario(dataset, "payout_partial_settlement_v11"),
        ),
    )
    bad = _with_submit(
        "payout_partial_settlement_v11", resolution_code="PAID_IN_FULL", proposed_action=None
    )
    engines = (onramp_engine, marketplace_engine)

    good = run_cases(
        _runner(
            engines,
            onramp_committed,
            marketplace_committed,
            load_scripts(CASES / "investigations.yaml"),
        ),
        subset,
        truth,
        tmp_path / "good",
        {"run_id": "good"},
    )
    wrong = run_cases(
        _runner(engines, onramp_committed, marketplace_committed, bad),
        subset,
        truth,
        tmp_path / "bad",
        {"run_id": "bad"},
    )

    records = [
        json.loads(line) for line in (tmp_path / "good" / "results.jsonl").read_text().splitlines()
    ]
    assert [r["scenario_id"] for r in records] == [
        "payout_paid_in_full",
        "payout_partial_settlement_v11",
    ]
    assert all(r["schema_version"] == 1 and r["grade"]["outcome_correct"] for r in records)
    manifest = json.loads((tmp_path / "good" / "manifest.json").read_text())
    assert manifest["run_id"] == "good"
    assert "finished_at" in manifest
    assert json.loads((tmp_path / "good" / "summary.json").read_text()) == good.summary
    assert gate_failures(good.summary, 1.0) == []
    assert wrong.summary["wrong_outcomes"] == ["payout_partial_settlement_v11"]
    assert gate_failures(wrong.summary, 1.0) == ["outcome accuracy 0.500 < 1.000"]
