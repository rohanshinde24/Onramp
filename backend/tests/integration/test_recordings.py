"""Recorded live answers: paid once, replayed for free while their inputs still match."""

from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine

from onramp.agent.model import Message, ModelResponse, ToolDefinition, Usage
from onramp.evaluation.case_grading import grade_case
from onramp.evaluation.recordings import RecordedModel, RecordingKey, RecordingStore
from onramp.evaluation.runner import CaseEvaluationRunner, RunStatus, ScenarioRun
from onramp.evaluation.scenarios import load_scenarios
from onramp.evaluation.scripted import load_scripts
from onramp.evaluation.spend import SpendMeter
from tests.integration.eval_support import ROOT, Sessions, make_runner

CASES = ROOT / "eval" / "cases"
SCENARIO = "payout_partial_settlement_v11"
MODEL = "claude-haiku-5-5@low"


class _FakeProvider:
    """Answers like the reference script by turn number, and counts calls like a provider."""

    def __init__(self) -> None:
        self.name = MODEL
        self.calls = 0
        self._script = load_scripts(CASES / "investigations.yaml").investigations[SCENARIO]

    def respond(
        self, system: str, transcript: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelResponse:
        self.calls += 1
        turn = sum(m.role == "assistant" for m in transcript)
        scripted = load_scripts(CASES / "investigations.yaml").model_for(SCENARIO)
        for _ in range(turn):
            scripted.respond(system, transcript, tools)
        response = scripted.respond(system, transcript, tools)
        return ModelResponse(
            tool_calls=response.tool_calls,
            usage=Usage(input_tokens=5_000, output_tokens=500, cache_read_tokens=3_000),
        )


@pytest.fixture
def run_recorded(
    onramp_engine: Engine,
    marketplace_engine: Engine,
    onramp_committed: Sessions,
    marketplace_committed: Sessions,
    tmp_path: Path,
) -> tuple[RecordingStore, _FakeProvider, object]:
    store = RecordingStore(tmp_path / "recordings")
    provider = _FakeProvider()
    scenarios, truth = load_scenarios(CASES / "scenarios.yaml", CASES / "truth.yaml")
    (scenario,) = [s for s in scenarios.scenarios if s.id == SCENARIO]
    (label,) = [t for t in truth.truths if t.id == SCENARIO]

    def run() -> tuple[ScenarioRun, SpendMeter, bool]:
        runner: CaseEvaluationRunner = make_runner(
            (onramp_engine, marketplace_engine),
            onramp_committed,
            marketplace_committed,
            load_scripts(CASES / "investigations.yaml"),
        )
        meter = SpendMeter(cap_usd=Decimal("1"))
        runner._models = lambda sid: RecordedModel(
            RecordingKey(MODEL, "investigator-v1", sid), store, lambda: provider
        )
        runner._meter = meter
        result = runner.run(scenario)
        assert result.observation is not None
        return result, meter, grade_case(result.observation, label).outcome_correct

    return store, provider, run


def test_a_second_run_replays_every_answer_for_free(run_recorded: tuple) -> None:  # type: ignore[type-arg]
    _store, provider, run = run_recorded

    first, first_meter, first_correct = run()
    calls_after_first = provider.calls
    second, second_meter, second_correct = run()

    assert first.status is RunStatus.SUCCESS
    assert first_correct
    assert calls_after_first == 5
    assert first_meter.spent_usd > 0
    assert provider.calls == calls_after_first  # nothing asked live again
    assert second_meter.spent_usd == 0
    assert second.cost_usd == pytest.approx(first.cost_usd)
    assert second.replayed_usd == pytest.approx(first.cost_usd)
    assert second_correct


def test_a_changed_turn_is_asked_again_from_that_turn_on(run_recorded: tuple) -> None:  # type: ignore[type-arg]
    store, provider, run = run_recorded
    run()
    key = RecordingKey(MODEL, "investigator-v1", SCENARIO)
    recording = store.load(key)
    # Pretend the world behind turn 3 changed: its recorded input no longer matches.
    recording.turns[2] = recording.turns[2].model_copy(update={"digest": "stale"})
    store.save(key, recording)
    calls_before = provider.calls

    second, meter, correct = run()

    assert provider.calls - calls_before == 3  # turns 3, 4 and 5 again; 1 and 2 replayed
    assert 0 < meter.spent_usd < Decimal(str(second.cost_usd))
    assert correct
    assert [t.digest for t in store.load(key).turns][2] != "stale"
