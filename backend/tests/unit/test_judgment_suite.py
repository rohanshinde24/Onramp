from pathlib import Path

from onramp.evaluation.judgment import render
from onramp.evaluation.scenarios import load_scenarios, validate_truth
from onramp.evaluation.worlds import WORLDS
from onramp.policy.corpus import load_corpus

ROOT = Path(__file__).resolve().parents[3]
SUITE = ROOT / "eval" / "judgment"


def test_committed_yaml_is_exactly_what_the_label_rules_generate() -> None:
    scenarios, truth = render()

    assert (SUITE / "scenarios.yaml").read_text() == scenarios
    assert (SUITE / "truth.yaml").read_text() == truth


def test_labels_validate_and_every_world_exists() -> None:
    scenarios, truth = load_scenarios(SUITE / "scenarios.yaml", SUITE / "truth.yaml")

    validate_truth(truth, load_corpus(ROOT / "policies"))
    assert {s.world_fixture for s in scenarios.scenarios} <= WORLDS.keys()
    escalations = sum(t.should_escalate for t in truth.truths)
    # Always escalating must not look like good judgment.
    assert 0.4 < escalations / len(truth.truths) < 0.6
