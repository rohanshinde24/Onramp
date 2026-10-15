"""A Promptfoo view of recorded evaluation runs: model by prompt, case by case.

Promptfoo is used only to display the matrix. Its provider replays each run's stored outcome for
a case and its assertion checks the stored grade, so rendering the view makes no model call and
costs nothing. The numbers come from the runs themselves; Promptfoo adds the side-by-side view.
"""

import json
import os
from pathlib import Path
from typing import Any

import yaml

PROVIDER = Path("eval") / "promptfoo" / "replay_provider.js"


def export(run_dirs: list[Path], out: Path, root: Path) -> tuple[Path, Path]:
    """Write ``results.json`` and ``promptfooconfig.yaml`` for the given runs into ``out``. Paths
    in the config are relative to ``root``, where Promptfoo runs."""
    results: dict[str, dict[str, dict[str, Any]]] = {}
    models: dict[str, None] = {}
    prompts: dict[str, None] = {}
    scenarios: dict[str, None] = {}
    for run_dir in run_dirs:
        manifest = json.loads((run_dir / "manifest.json").read_text())
        model, prompt = str(manifest["model"]), str(manifest["prompt_version"])
        models[model] = None
        prompts[prompt] = None
        cell = results.setdefault(f"{model}|{prompt}", {})
        for line in (run_dir / "results.jsonl").read_text().splitlines():
            record = json.loads(line)
            grade = record.get("grade") or {}
            scenarios[record["scenario_id"]] = None
            cell[record["scenario_id"]] = {
                "outcome": grade.get("actual_resolution", record["status"]),
                "expected": grade.get("expected_resolution"),
                "correct": bool(grade.get("outcome_correct")),
                "cost_usd": record.get("cost_usd", 0.0),
                "run_id": manifest["run_id"],
            }
    out.mkdir(parents=True, exist_ok=True)
    results_path = out / "results.json"
    results_path.write_text(json.dumps(results, indent=1, sort_keys=True))
    config = {
        "description": "Recorded Onramp judgment-suite runs, model by prompt (no model calls)",
        "prompts": [{"label": p, "raw": p} for p in prompts],
        "providers": [
            {
                # Promptfoo resolves the provider relative to the config file.
                "id": f"file://{os.path.relpath(root / PROVIDER, out)}",
                "label": m,
                "config": {"model": m, "results": str(results_path.relative_to(root))},
            }
            for m in models
        ],
        "defaultTest": {
            "assert": [{"type": "javascript", "value": "JSON.parse(output).correct === true"}]
        },
        "tests": [{"vars": {"scenario_id": s}} for s in scenarios],
    }
    config_path = out / "promptfooconfig.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False, width=100))
    return results_path, config_path
