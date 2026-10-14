from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

import pytest

from onramp.agent.model import Message, ModelResponse, ToolDefinition, Usage
from onramp.entrypoints import case_eval
from onramp.evaluation.spend import (
    PRICES,
    MeteredModel,
    SpendCapReachedError,
    SpendCapRequiredError,
    SpendMeter,
    UnpricedModelError,
    cost,
    price_for,
)


class _Priced:
    """A model that reports usage like a live one, without calling anything."""

    def __init__(self, name: str, usage: Usage) -> None:
        self.name = name
        self._usage = usage
        self.calls = 0

    def respond(
        self, system: str, transcript: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelResponse:
        self.calls += 1
        return ModelResponse(usage=self._usage)


def test_cost_prices_cached_and_uncached_input_and_output_separately() -> None:
    usage = Usage(
        input_tokens=10_000, output_tokens=1_000, cache_read_tokens=6_000, cache_write_tokens=2_000
    )
    sonnet = PRICES["claude-sonnet-5-5"]

    # 2k uncached x $2, 2k written x $2.50, 6k read x $0.20, 1k output x $10 (per million)
    assert cost(usage, sonnet) == Decimal("0.0002") * 20 + Decimal("0.0002") * 25 + Decimal(
        "0.0012"
    ) + Decimal("0.01")
    assert cost(usage, sonnet, batch=True) == cost(usage, sonnet) / 2
    assert cost(usage, None) == 0


def test_model_names_resolve_to_prices_and_unknown_models_are_refused() -> None:
    assert price_for("claude-haiku-5-5@low") == PRICES["claude-haiku-5-5"]
    assert price_for("scripted:reference:v2") is None
    with pytest.raises(UnpricedModelError):
        price_for("claude-imaginary-1@high")


def test_a_priced_model_needs_a_cap_and_stops_after_reaching_it() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=0)  # $2.00 per call on Sonnet
    with pytest.raises(SpendCapRequiredError):
        MeteredModel(_Priced("claude-sonnet-5-5@low", usage), SpendMeter())

    inner = _Priced("claude-sonnet-5-5@low", usage)
    meter = SpendMeter(cap_usd=Decimal("3"))
    model = MeteredModel(inner, meter)
    model.respond("s", (), ())
    model.respond("s", (), ())  # $2 spent is under the cap, so this call is made
    with pytest.raises(SpendCapReachedError):
        model.respond("s", (), ())

    assert inner.calls == 2
    assert meter.spent_usd == Decimal("4")  # overshoot is at most one call


def test_live_runs_are_refused_without_a_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["onramp-case-eval", "--model", "claude-haiku-5-5"])

    with pytest.raises(SystemExit, match="--max-cost"):
        case_eval.main()


def test_batch_runs_are_refused_without_a_live_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["onramp-case-eval", "--batch"])

    with pytest.raises(SystemExit, match="--batch records live answers"):
        case_eval.main()


def test_a_live_model_option_selects_the_recorded_live_model(tmp_path: Path) -> None:
    from argparse import Namespace

    from pydantic import SecretStr

    from onramp.evaluation.recordings import RecordedModel
    from onramp.evaluation.scripted import ScriptSet

    args = Namespace(
        model="claude-haiku-5-5",
        effort="low",
        max_cost=Decimal("0.05"),
        batch=False,
        prompt="investigator-v1",
        repeat=0,
        recordings=tmp_path,
    )
    scripts = ScriptSet(version=1, model="reference", investigations={})

    models, name = case_eval.select_models(args, SecretStr("not-a-real-key"), scripts)

    assert name == "claude-haiku-5-5@low"
    assert isinstance(models("any_scenario"), RecordedModel)
    args.model = None
    assert case_eval.select_models(args, None, scripts)[1] == "scripted:reference:v1"
