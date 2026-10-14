"""Comparing evaluation runs with DuckDB (ADR-009), and the regression gate.

A run directory holds ``results.jsonl``, one record per scenario. DuckDB reads those files
directly; this module never loads them into the application's databases. A comparison pairs the
two runs by scenario, so each case's difficulty cancels out, and reports which cases a change
fixed and which it broke.

The gate fails a candidate when, against its baseline:

* any graded case has more safety violations than before, in total,
* accuracy is lower beyond doubt: the 95% paired bootstrap interval of the difference lies
  entirely below zero,
* cost per correct outcome rose by more than an allowed fraction, when a limit is given.

A run that did not finish, or was stopped by its spending cap, is not comparable and fails too.
"""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import duckdb

from onramp.evaluation.stats import PairedDifference, paired_bootstrap

RESULTS = "results.jsonl"
BASELINE_FILES = ("manifest.json", "summary.json", RESULTS)

# One row per graded scenario. Ungraded runs (failures) count as incorrect: a change that makes
# a case fail to run has broken it.
_CASES = """
    SELECT
        scenario_id,
        status,
        coalesce(grade.outcome_correct, false) AS correct,
        coalesce(len(grade.safety_violations), 0) AS violations,
        coalesce(cost_usd, 0) AS cost_usd
    FROM read_json_auto(?, format = 'newline_delimited')
"""


@dataclass(frozen=True)
class RunMetrics:
    run_id: str
    cases: int
    correct: int
    safety_violations: int
    cost_usd: float

    @property
    def accuracy(self) -> float:
        return self.correct / self.cases if self.cases else 0.0

    @property
    def cost_per_correct(self) -> float | None:
        return self.cost_usd / self.correct if self.correct else None


@dataclass(frozen=True)
class Comparison:
    base: RunMetrics
    candidate: RunMetrics
    shared: int
    fixed: tuple[str, ...]
    broken: tuple[str, ...]
    only_in_base: tuple[str, ...]
    only_in_candidate: tuple[str, ...]
    accuracy: PairedDifference | None
    incomplete: tuple[str, ...]


def write_parquet(run_dir: Path) -> Path:
    """The run's records as Parquet beside the canonical JSONL, for analytics."""
    target = run_dir / "results.parquet"
    source, destination = _quoted(run_dir / RESULTS), _quoted(target)
    # COPY takes no bound parameters; both paths are the run's own, with quotes escaped.
    statement = f"COPY (SELECT * FROM read_json_auto('{source}', format = 'newline_delimited')) TO '{destination}' (FORMAT parquet)"  # noqa: E501, S608
    with duckdb.connect() as db:
        db.execute(statement)
    return target


def compare(base_dir: Path, candidate_dir: Path) -> Comparison:
    with duckdb.connect() as db:
        db.execute(f"CREATE TABLE base AS {_CASES}", [str(base_dir / RESULTS)])
        db.execute(f"CREATE TABLE candidate AS {_CASES}", [str(candidate_dir / RESULTS)])
        flips = db.execute(
            """
            SELECT b.scenario_id, b.correct, c.correct
            FROM base b JOIN candidate c USING (scenario_id)
            WHERE b.correct <> c.correct
            ORDER BY b.scenario_id
            """
        ).fetchall()
        only_base = _column(
            db, "SELECT scenario_id FROM base ANTI JOIN candidate USING (scenario_id) ORDER BY 1"
        )
        only_candidate = _column(
            db, "SELECT scenario_id FROM candidate ANTI JOIN base USING (scenario_id) ORDER BY 1"
        )
        pairs = db.execute(
            """
            SELECT b.scenario_id, b.correct::DOUBLE, c.correct::DOUBLE
            FROM base b JOIN candidate c USING (scenario_id)
            """
        ).fetchall()
        base_metrics = _metrics(db, "base", base_dir)
        candidate_metrics = _metrics(db, "candidate", candidate_dir)
    candidate_scores = {sid: c for sid, _, c in pairs}
    base_scores = {sid: b for sid, b, _ in pairs}
    return Comparison(
        base=base_metrics,
        candidate=candidate_metrics,
        shared=len(pairs),
        fixed=tuple(sid for sid, b, c in flips if c and not b),
        broken=tuple(sid for sid, b, c in flips if b and not c),
        only_in_base=only_base,
        only_in_candidate=only_candidate,
        accuracy=paired_bootstrap(candidate_scores, base_scores) if pairs else None,
        incomplete=tuple(
            f"{name} stopped: {stopped}"
            for name, directory in (("base", base_dir), ("candidate", candidate_dir))
            if (stopped := _summary(directory).get("stopped"))
        ),
    )


def regressions(comparison: Comparison, *, max_cost_increase: float | None = None) -> list[str]:
    """Why the candidate fails the gate against its base. Empty means it passes."""
    found = list(comparison.incomplete)
    base, candidate = comparison.base, comparison.candidate
    if candidate.safety_violations > base.safety_violations:
        found.append(
            f"safety violations rose from {base.safety_violations} to {candidate.safety_violations}"
        )
    if comparison.accuracy is not None and comparison.accuracy.high < 0:
        found.append(
            f"accuracy fell by {-comparison.accuracy.mean:.3f} "
            f"(95% CI {comparison.accuracy.low:+.3f} to {comparison.accuracy.high:+.3f})"
        )
    if max_cost_increase is not None:
        before, after = base.cost_per_correct, candidate.cost_per_correct
        if before and after is not None and after > before * (1 + max_cost_increase):
            found.append(
                f"cost per correct outcome rose from ${before:.4f} to ${after:.4f}, more than "
                f"{max_cost_increase:.0%}"
            )
        elif before and after is None:
            found.append("the candidate got no case right, so it has no cost per correct outcome")
    return found


def store_baseline(run_dir: Path, target: Path) -> None:
    """Copy a finished run's records and manifest to a committed baseline directory."""
    summary = _summary(run_dir)
    if summary.get("stopped"):
        raise ValueError(f"{run_dir} did not finish and cannot be a baseline")
    target.mkdir(parents=True, exist_ok=False)
    for name in BASELINE_FILES:
        shutil.copy2(run_dir / name, target / name)


def _metrics(db: duckdb.DuckDBPyConnection, table: str, run_dir: Path) -> RunMetrics:
    row = db.execute(
        f"SELECT count(*), count(*) FILTER (WHERE correct), sum(violations), sum(cost_usd) "  # noqa: S608 - fixed table name
        f"FROM {table}"
    ).fetchone()
    cases, correct, violations, cost = row if row else (0, 0, 0, 0.0)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    return RunMetrics(
        run_id=str(manifest.get("run_id", run_dir.name)),
        cases=int(cases),
        correct=int(correct),
        safety_violations=int(violations or 0),
        cost_usd=float(cost or 0.0),
    )


def _column(db: duckdb.DuckDBPyConnection, query: str) -> tuple[str, ...]:
    return tuple(str(row[0]) for row in db.execute(query).fetchall())


def _summary(run_dir: Path) -> dict[str, object]:
    path = run_dir / "summary.json"
    return json.loads(path.read_text()) if path.exists() else {"stopped": "no summary"}


def _quoted(path: Path) -> str:
    return str(path).replace("'", "''")
