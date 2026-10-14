"""Case-evaluation run artifacts (ADR-009): manifest, per-case JSONL and summary.

``results.jsonl`` is written one record per scenario as the run proceeds, so an interrupted
run still leaves what it finished. The manifest records what produced the numbers: commit,
dirty tree, dataset and script versions, and the run's known limitations.
"""

import json
import shutil
import subprocess
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from onramp.evaluation.case_grading import (
    CaseGrade,
    CaseObservation,
    grade_case,
    summarize_cases,
    to_record,
)
from onramp.evaluation.runner import CaseEvaluationRunner, RunStatus, ScenarioRun
from onramp.evaluation.scenarios import ScenarioSet, TruthSet
from onramp.evaluation.spend import SpendCapReachedError

RECORD_SCHEMA_VERSION = 1

LIMITATIONS = (
    "Investigations are scripted; the run measures the application around the model, not any "
    "model's judgment. Token counts and investigation latency are therefore not meaningful.",
    "A scripted reviewer accepts every proposal and approves every action as shown, so the run "
    "measures what the system recommends and executes when nobody overrides it.",
    "Waits end at their deadline on a virtual clock. Temporal timers, signals and recovery are "
    "covered by the workflow tests, not by this run.",
    "Policy search uses a deterministic keyword retriever over PostgreSQL, not the production "
    "dense retriever.",
    "I wrote the worlds, the labels and the scripts; this checks behavior, it is not an "
    "independent benchmark.",
)


def git_state(root: Path) -> tuple[str, bool]:
    """The checked-out commit and whether the working tree has uncommitted changes."""
    git = shutil.which("git")
    if git is None:
        return "", False

    def run(*args: str) -> str:
        result = subprocess.run(  # noqa: S603 - resolved git binary with fixed arguments
            [git, *args], cwd=root, capture_output=True, text=True, check=False
        )
        return result.stdout.strip()

    return run("rev-parse", "HEAD"), bool(run("status", "--porcelain"))


@dataclass(frozen=True)
class RunOutcome:
    runs: tuple[ScenarioRun, ...]
    grades: tuple[CaseGrade, ...]
    summary: dict[str, Any]


def run_cases(
    runner: CaseEvaluationRunner,
    scenarios: ScenarioSet,
    truth: TruthSet,
    out: Path,
    manifest: dict[str, Any],
) -> RunOutcome:
    """Run every scenario, writing each record as it finishes, then the summary."""
    labels = {label.id: label for label in truth.truths}
    out.mkdir(parents=True)
    _write_json(out / "manifest.json", manifest)
    runs, grades = [], []
    stopped: str | None = None
    with (out / "results.jsonl").open("w") as results:
        for scenario in scenarios.scenarios:
            try:
                run = runner.run(scenario)
            except SpendCapReachedError as exc:
                stopped = f"spending cap reached before {scenario.id}: {exc}"
                break
            grade = None
            if run.status is RunStatus.SUCCESS and run.observation is not None:
                grade = grade_case(run.observation, labels[scenario.id])
                grades.append(grade)
            runs.append(run)
            results.write(json.dumps(_record(manifest["run_id"], run, grade)) + "\n")
            results.flush()
    summary = summarize_runs(runs, grades) | {
        "scenarios_planned": len(scenarios.scenarios),
        "stopped": stopped,
    }
    _write_json(out / "summary.json", summary)
    _write_json(out / "manifest.json", {**manifest, "finished_at": datetime.now(UTC).isoformat()})
    _write_parquet(out)
    return RunOutcome(tuple(runs), tuple(grades), summary)


def summarize_runs(runs: Sequence[ScenarioRun], grades: Sequence[CaseGrade]) -> dict[str, Any]:
    statuses = Counter(run.status.value for run in runs)
    total_cost = sum(run.cost_usd for run in runs)
    replayed = sum(run.replayed_usd for run in runs)
    correct = sum(grade.outcome_correct for grade in grades)
    return {
        "scenarios": len(runs),
        "status": {status.value: statuses.get(status.value, 0) for status in RunStatus},
        "cost_usd": round(total_cost, 6),
        "paid_this_run_usd": round(total_cost - replayed, 6),
        "cost_per_correct_outcome_usd": round(total_cost / correct, 6) if correct else None,
        "graded": summarize_cases(grades) if grades else None,
        "wrong_outcomes": sorted(g.scenario_id for g in grades if not g.outcome_correct),
        "safety_violations": {
            g.scenario_id: list(g.safety_violations) for g in grades if g.safety_violations
        },
    }


def gate_failures(summary: dict[str, Any], min_outcome_accuracy: float) -> list[str]:
    """Why this run should fail a gate. Empty means it passes."""
    failures = [f"stopped early: {summary['stopped']}"] if summary.get("stopped") else []
    failures += [
        f"{count} {status} run(s)"
        for status, count in summary["status"].items()
        if status != RunStatus.SUCCESS and count
    ]
    if summary["safety_violations"]:
        failures.append(f"safety violations: {summary['safety_violations']}")
    graded = summary["graded"]
    accuracy = graded["outcome_accuracy"] if graded else 0.0
    if accuracy < min_outcome_accuracy:
        failures.append(f"outcome accuracy {accuracy:.3f} < {min_outcome_accuracy:.3f}")
    return failures


def _record(run_id: str, run: ScenarioRun, grade: CaseGrade | None) -> dict[str, Any]:
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "run_id": run_id,
        "scenario_id": run.scenario_id,
        "status": run.status.value,
        "error": run.error,
        "case_id": run.case_id,
        "final_state": run.final_state,
        "faults": run.faults,
        "cost_usd": run.cost_usd,
        "replayed_usd": run.replayed_usd,
        "steps": run.steps,
        "wall_ms": run.wall_ms,
        "observation": _observation(run.observation) if run.observation else None,
        "grade": to_record(grade) if grade else None,
    }


def _observation(observation: CaseObservation) -> dict[str, Any]:
    # Every passage ID in the corpus adds nothing per record; citations are sorted for diffs.
    data = observation.model_dump(mode="json", exclude={"known_passages"})
    data["citations"] = sorted(data["citations"])
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _write_parquet(out: Path) -> None:
    """Parquet beside the JSONL when DuckDB is installed (the ``eval`` extra)."""
    try:
        from onramp.evaluation.compare import write_parquet
    except ImportError:
        return
    write_parquet(out)
