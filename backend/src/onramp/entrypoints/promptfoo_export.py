"""Prepare a Promptfoo view of recorded runs: ``onramp-promptfoo-export RUN_DIR...``.

Writes ``artifacts/promptfoo/results.json`` and ``promptfooconfig.yaml`` with paths relative to
the repository root, where ``make promptfoo-view`` runs Promptfoo in a Node container.
"""

import argparse
from pathlib import Path

from onramp.evaluation.promptfoo import export

ROOT = Path(__file__).resolve().parents[4]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+", type=Path)
    args = parser.parse_args()
    results, config = export(
        [run.resolve() for run in args.runs], ROOT / "artifacts" / "promptfoo", ROOT
    )
    print(f"wrote {results} and {config}")


if __name__ == "__main__":
    main()
