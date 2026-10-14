from sqlalchemy import Engine

from onramp.evaluation.scenarios import load_scenarios
from onramp.evaluation.scripted import load_scripts
from tests.integration.eval_support import ROOT, Sessions, make_runner

SUITE = ROOT / "eval" / "judgment"


def test_every_judgment_scenario_reaches_the_investigator(
    onramp_engine: Engine,
    marketplace_engine: Engine,
    onramp_committed: Sessions,
    marketplace_committed: Sessions,
) -> None:
    scenarios, _ = load_scenarios(SUITE / "scenarios.yaml", SUITE / "truth.yaml")
    runner = make_runner(
        (onramp_engine, marketplace_engine),
        onramp_committed,
        marketplace_committed,
        load_scripts(ROOT / "eval" / "cases" / "investigations.yaml"),
    )

    missed = [s.id for s in scenarios.scenarios if runner.snapshot(s) is None]

    assert missed == []
