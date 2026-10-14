"""Investigations recorded through batches against snapshots replay through the real runner."""

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import Engine

from onramp.agent.anthropic_model import AnthropicChatModel
from onramp.agent.model import ChatModel
from onramp.evaluation.batching import BatchOutcome, record_investigations
from onramp.evaluation.case_grading import grade_case
from onramp.evaluation.recordings import RecordedModel, RecordingKey, RecordingStore
from onramp.evaluation.scenarios import load_scenarios
from onramp.evaluation.scripted import load_scripts
from onramp.evaluation.spend import SpendMeter
from tests.integration.eval_support import ROOT, Sessions, make_runner

CASES = ROOT / "eval" / "cases"
# The scenarios that need the investigator, and the subject each one's case is about.
SUBJECTS = {
    "payout_partial_settlement_v11": "submission:s_107",
    "onboarding_unverified_upload": "worker:w_eli",
    "onboarding_tax_form_arrives_verified": "worker:w_cho",
}


class _ScriptedBatches:
    """A batch endpoint that answers like the reference scripts, recognizing each case by the
    subject its get_case result names."""

    def __init__(self) -> None:
        self.scripts = load_scripts(CASES / "investigations.yaml")
        self.batch_sizes: list[int] = []

    def submit(self, requests: dict[str, dict[str, Any]]) -> dict[str, BatchOutcome]:
        self.batch_sizes.append(len(requests))
        return {key: BatchOutcome(message=self._answer(params)) for key, params in requests.items()}

    def _answer(self, params: dict[str, Any]) -> Any:
        messages = params["messages"]
        turn = sum(m["role"] == "assistant" for m in messages)
        seen = json.dumps(messages, default=vars)
        scenario = next(
            (sid for sid, subject in SUBJECTS.items() if subject in seen),
            "payout_partial_settlement_v11",  # turn 0 is get_case for every script
        )
        calls = self.scripts.investigations[scenario][turn]
        return SimpleNamespace(
            stop_reason="tool_use",
            content=[
                SimpleNamespace(type="tool_use", id=f"t{turn}_{i}", name=c.tool, input=c.args)
                for i, c in enumerate(calls)
            ],
            usage=SimpleNamespace(
                input_tokens=2_000,
                output_tokens=200,
                cache_read_input_tokens=1_000,
                cache_creation_input_tokens=0,
            ),
        )


class _NoLiveCalls:
    name = "claude-haiku-5-5@medium"

    def respond(self, *args: Any) -> Any:
        raise AssertionError("every answer should have been replayed")


def test_batched_snapshot_investigations_replay_through_the_real_runner(
    onramp_engine: Engine,
    marketplace_engine: Engine,
    onramp_committed: Sessions,
    marketplace_committed: Sessions,
    tmp_path: Path,
) -> None:
    scenarios, truth = load_scenarios(CASES / "scenarios.yaml", CASES / "truth.yaml")
    labels = {label.id: label for label in truth.truths}
    runner = make_runner(
        (onramp_engine, marketplace_engine),
        onramp_committed,
        marketplace_committed,
        load_scripts(CASES / "investigations.yaml"),
    )
    chosen = [s for s in scenarios.scenarios if s.id in SUBJECTS or s.id == "payout_paid_in_full"]
    snapshots = [snap for s in chosen if (snap := runner.snapshot(s)) is not None]
    assert sorted(s.scenario_id for s in snapshots) == sorted(SUBJECTS)  # fast path needs none

    store = RecordingStore(tmp_path / "recordings")
    model = AnthropicChatModel(client=object(), model="claude-haiku-5-5", effort="medium")
    batches = _ScriptedBatches()
    meter = SpendMeter(cap_usd=Decimal("1"))
    result = record_investigations(
        snapshots,
        model=model,
        prompt_version="investigator-v1",
        store=store,
        meter=meter,
        submitter=batches,
    )

    assert result.failures == {}
    assert batches.batch_sizes[0] == 3  # every investigation's first turn in one batch
    assert len(batches.batch_sizes) == 5  # the longest investigation has five turns
    assert meter.spent_usd > 0

    def replay(scenario_id: str) -> ChatModel:
        key = RecordingKey(model.name, "investigator-v1", scenario_id)
        return RecordedModel(key, store, _NoLiveCalls)

    runner._models = replay
    runner._meter = SpendMeter(cap_usd=Decimal("1"))
    for scenario in chosen:
        run = runner.run(scenario)
        assert run.observation is not None, run.error
        assert grade_case(run.observation, labels[scenario.id]).outcome_correct, scenario.id
        if scenario.id in SUBJECTS:
            assert run.replayed_usd == run.cost_usd > 0  # recorded at batch prices, paid once
    assert runner._meter.spent_usd == 0
