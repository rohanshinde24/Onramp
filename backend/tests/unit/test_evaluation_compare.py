import json
from pathlib import Path

import duckdb
import pytest

from onramp.evaluation.compare import compare, regressions, store_baseline, write_parquet


def _run(
    directory: Path,
    correct: list[bool],
    *,
    violations: int = 0,
    cost: float = 0.01,
    stopped: str | None = None,
    failed: tuple[int, ...] = (),
) -> Path:
    directory.mkdir(parents=True)
    with (directory / "results.jsonl").open("w") as out:
        for i, ok in enumerate(correct):
            graded = i not in failed
            record = {
                "scenario_id": f"case_{i:02}",
                "status": "success" if graded else "app_failure",
                "cost_usd": cost,
                "grade": {
                    "outcome_correct": ok,
                    "safety_violations": ["duplicate_marketplace_write"] if i < violations else [],
                }
                if graded
                else None,
            }
            out.write(json.dumps(record) + "\n")
    (directory / "manifest.json").write_text(json.dumps({"run_id": directory.name}))
    (directory / "summary.json").write_text(json.dumps({"stopped": stopped}))
    return directory


def test_identical_runs_pass_and_report_no_changes(tmp_path: Path) -> None:
    base = _run(tmp_path / "base", [True] * 20)
    candidate = _run(tmp_path / "candidate", [True] * 20)

    comparison = compare(base, candidate)

    assert (comparison.fixed, comparison.broken) == ((), ())
    assert comparison.base.accuracy == comparison.candidate.accuracy == 1.0
    assert regressions(comparison) == []


def test_a_clear_accuracy_drop_fails_and_names_the_broken_cases(tmp_path: Path) -> None:
    base = _run(tmp_path / "base", [True] * 30)
    candidate = _run(tmp_path / "candidate", [True] * 20 + [False] * 10)

    comparison = compare(base, candidate)
    failures = regressions(comparison)

    assert comparison.broken == tuple(f"case_{i}" for i in range(20, 30))
    assert comparison.accuracy is not None
    assert comparison.accuracy.high < 0
    assert failures[0].startswith("accuracy fell by 0.333")


def test_one_flip_is_reported_but_is_not_proof_of_a_regression(tmp_path: Path) -> None:
    base = _run(tmp_path / "base", [True] * 30)
    candidate = _run(tmp_path / "candidate", [True] * 29 + [False])

    comparison = compare(base, candidate)

    assert comparison.broken == ("case_29",)
    assert regressions(comparison) == []


def test_failed_runs_count_as_wrong_and_safety_and_cost_regressions_fail(tmp_path: Path) -> None:
    base = _run(tmp_path / "base", [True] * 10, cost=0.01)
    candidate = _run(tmp_path / "candidate", [True] * 10, violations=1, cost=0.02, failed=(9,))

    comparison = compare(base, candidate)
    failures = regressions(comparison, max_cost_increase=0.5)

    assert comparison.broken == ("case_09",)
    assert any("safety violations rose from 0 to 1" in f for f in failures)
    assert any("cost per correct outcome rose" in f for f in failures)


def test_a_stopped_run_cannot_pass_or_become_a_baseline(tmp_path: Path) -> None:
    base = _run(tmp_path / "base", [True] * 5)
    stopped = _run(tmp_path / "stopped", [True] * 5, stopped="spending cap reached")

    assert regressions(compare(base, stopped)) == ["candidate stopped: spending cap reached"]
    with pytest.raises(ValueError, match="did not finish"):
        store_baseline(stopped, tmp_path / "baselines" / "x")
    store_baseline(base, tmp_path / "baselines" / "good")
    assert (tmp_path / "baselines" / "good" / "results.jsonl").exists()


def test_parquet_holds_the_same_records(tmp_path: Path) -> None:
    run = _run(tmp_path / "run", [True, False, True])

    parquet = write_parquet(run)

    with duckdb.connect() as db:
        rows = db.execute(
            "SELECT count(*), count(*) FILTER (WHERE grade.outcome_correct) FROM read_parquet(?)",
            [str(parquet)],
        ).fetchone()
    assert rows == (3, 2)
