"""Compare two evaluation runs: ``onramp-case-compare BASE CANDIDATE``.

Either may be a run under ``artifacts/`` or a baseline under ``eval/baselines/``. Prints both
runs' accuracy, safety and cost, the cases the candidate fixed and broke, and the paired
accuracy difference with its confidence interval. Exits non-zero when the candidate regressed.

``onramp-case-compare --store-baseline RUN NAME`` copies a finished run to
``eval/baselines/<suite>/<NAME>/`` so later runs can be compared against it.
"""

import argparse
import sys
from pathlib import Path

from onramp.evaluation.compare import Comparison, RunMetrics, compare, regressions, store_baseline

ROOT = Path(__file__).resolve().parents[4]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs=2, type=Path, metavar=("BASE", "CANDIDATE"))
    parser.add_argument(
        "--max-cost-increase",
        type=float,
        help="fail if cost per correct outcome rises by more than this fraction (0.2 = 20%%)",
    )
    parser.add_argument(
        "--store-baseline",
        action="store_true",
        help="treat the arguments as RUN and NAME and store the run as a baseline",
    )
    args = parser.parse_args()

    if args.store_baseline:
        run, name = args.paths
        target = ROOT / "eval" / "baselines" / run.parent.name / str(name)
        store_baseline(run, target)
        print(f"stored {run} as {target.relative_to(ROOT)}")
        return

    comparison = compare(*args.paths)
    _print(comparison)
    failures = regressions(comparison, max_cost_increase=args.max_cost_increase)
    if failures:
        sys.exit("regression: " + "; ".join(failures))
    print("no regression")


def _print(comparison: Comparison) -> None:
    print(
        f"{'':12}{'run':22}{'cases':>7}{'correct':>9}{'accuracy':>10}{'safety':>8}{'$/correct':>11}"
    )
    for label, run in (("base", comparison.base), ("candidate", comparison.candidate)):
        print(
            f"{label:12}{run.run_id:22}{run.cases:>7}{run.correct:>9}"
            f"{run.accuracy:>10.3f}{run.safety_violations:>8}{_cost(run):>11}"
        )
    if comparison.accuracy is not None:
        a = comparison.accuracy
        print(
            f"accuracy difference over {comparison.shared} shared cases: {a.mean:+.3f} "
            f"(95% CI {a.low:+.3f} to {a.high:+.3f})"
        )
    for label, cases in (
        ("fixed", comparison.fixed),
        ("broken", comparison.broken),
        ("only in base", comparison.only_in_base),
        ("only in candidate", comparison.only_in_candidate),
    ):
        if cases:
            print(f"{label}: {', '.join(cases)}")


def _cost(run: RunMetrics) -> str:
    return "n/a" if run.cost_per_correct is None else f"{run.cost_per_correct:.4f}"


if __name__ == "__main__":
    main()
