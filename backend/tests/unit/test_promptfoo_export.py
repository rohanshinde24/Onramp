import json
from pathlib import Path

import yaml

from onramp.evaluation.promptfoo import export


def _run(directory: Path, model: str, prompt: str, correct: list[bool]) -> Path:
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(
        json.dumps({"run_id": directory.name, "model": model, "prompt_version": prompt})
    )
    (directory / "results.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "scenario_id": f"case_{i}",
                    "status": "success",
                    "cost_usd": 0.01,
                    "grade": {
                        "outcome_correct": ok,
                        "actual_resolution": "X",
                        "expected_resolution": "X" if ok else "Y",
                    },
                }
            )
            + "\n"
            for i, ok in enumerate(correct)
        )
    )
    return directory


def test_export_builds_a_model_by_prompt_matrix_with_repo_relative_paths(tmp_path: Path) -> None:
    root = tmp_path
    runs = [
        _run(root / "runs" / "a", "haiku@low", "v1", [True, False]),
        _run(root / "runs" / "b", "haiku@low", "v2", [True, True]),
        _run(root / "runs" / "c", "sonnet@low", "v1", [False, True]),
    ]

    results_path, config_path = export(runs, root / "artifacts" / "promptfoo", root)

    results = json.loads(results_path.read_text())
    config = yaml.safe_load(config_path.read_text())
    assert results["haiku@low|v1"]["case_1"]["correct"] is False
    assert [p["raw"] for p in config["prompts"]] == ["v1", "v2"]
    assert [p["label"] for p in config["providers"]] == ["haiku@low", "sonnet@low"]
    provider = config["providers"][0]
    assert provider["id"] == "file://../../eval/promptfoo/replay_provider.js"
    assert provider["config"]["results"] == "artifacts/promptfoo/results.json"
    assert [t["vars"]["scenario_id"] for t in config["tests"]] == ["case_0", "case_1"]
