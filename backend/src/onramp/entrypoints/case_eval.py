"""Run the whole-application case evaluation: ``onramp-case-eval``.

Recreates dedicated ``*_eval`` databases, runs every scenario in ``eval/cases/scenarios.yaml``
through the real case services with scripted investigations, grades each against
``eval/cases/truth.yaml``, and writes ``artifacts/case-eval/<run_id>/``. Exits non-zero when a
run fails the gate.

The evaluation package may not import the marketplace writer (ADR-008), so the services that
hold one are assembled here.
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

import onramp.models  # noqa: F401 - every table must be mapped before any session is used
from onramp.actions.evidence import EvidenceRequester
from onramp.actions.gateway import ActionGateway
from onramp.agent.model import ChatModel
from onramp.agent.prompts import PROMPT_VERSION
from onramp.approvals.service import ApprovalService
from onramp.cases.service import CaseService
from onramp.checks.service import CheckService
from onramp.config import AppEnv, get_settings
from onramp.db import migrate
from onramp.db.engine import make_engine, make_session_factory
from onramp.evaluation.report import LIMITATIONS, gate_failures, git_state, run_cases
from onramp.evaluation.runner import CaseEvaluationRunner, CaseServices, VirtualClock
from onramp.evaluation.scenarios import load_scenarios, validate_truth
from onramp.evaluation.scripted import KeywordPolicyRetriever, load_scripts
from onramp.evaluation.worlds import reset_databases
from onramp.investigations.service import InvestigationService
from onramp.marketplace.reader import SqlMarketplaceReader
from onramp.marketplace.writer import MarketplaceWriter, SqlMarketplaceWriter
from onramp.policy.corpus import load_corpus
from onramp.policy.search import PolicyRetriever
from onramp.proposals.service import ProposalService
from onramp.workflows.activities import AgentActivities, WorkflowActivities


def build_services(
    onramp: sessionmaker[Session],
    marketplace: sessionmaker[Session],
    retriever: PolicyRetriever,
    clock: VirtualClock,
    model: ChatModel,
    writer: MarketplaceWriter | None = None,
) -> CaseServices:
    reader = SqlMarketplaceReader(marketplace)
    writer = writer or SqlMarketplaceWriter(marketplace, clock)
    checks = CheckService(onramp, reader, EvidenceRequester(writer), clock)
    investigations = InvestigationService(onramp, reader, retriever, model, clock=clock)
    return CaseServices(
        cases=CaseService(onramp, reader, clock),
        workflow=WorkflowActivities(
            onramp, checks, ActionGateway(onramp, reader, writer, clock), clock
        ),
        agent=AgentActivities(onramp, investigations),
        proposals=ProposalService(onramp, clock),
        approvals=ApprovalService(onramp, clock),
    )


ROOT = Path(__file__).resolve().parents[4]
CASES = ROOT / "eval" / "cases"
COMMAND = "make case-eval"


def _recreate(url: str) -> str:
    """Drop and recreate ``<database>_eval`` next to the configured database, migrated."""
    target = make_url(url)
    name = f"{target.database}_eval"
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    return target.set(database=name).render_as_string(hide_password=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenarios", type=Path, default=CASES / "scenarios.yaml")
    parser.add_argument("--truth", type=Path, default=CASES / "truth.yaml")
    parser.add_argument("--scripts", type=Path, default=CASES / "investigations.yaml")
    parser.add_argument("--min-outcome-accuracy", type=float, default=1.0)
    args = parser.parse_args()

    settings = get_settings()
    if settings.app_env is AppEnv.STAGING:
        sys.exit("onramp-case-eval recreates databases and does not run in staging")
    scenarios, truth = load_scenarios(args.scenarios, args.truth)
    corpus = load_corpus(ROOT / "policies")
    validate_truth(truth, corpus)
    scripts = load_scripts(args.scripts)

    onramp_url = _recreate(str(settings.database_url))
    marketplace_url = _recreate(str(settings.marketplace_database_url))
    migrate.upgrade("onramp", onramp_url)
    migrate.upgrade("marketplace", marketplace_url)
    onramp_engine, marketplace_engine = make_engine(onramp_url), make_engine(marketplace_url)
    onramp, marketplace = (
        make_session_factory(onramp_engine),
        make_session_factory(marketplace_engine),
    )
    retriever = KeywordPolicyRetriever(onramp)
    runner = CaseEvaluationRunner(
        onramp,
        marketplace,
        lambda: reset_databases(onramp_engine, marketplace_engine),
        lambda clock, model: build_services(onramp, marketplace, retriever, clock, model),
        corpus,
        scripts.model_for,
    )

    started = datetime.now(UTC)
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    commit, dirty = git_state(ROOT)
    out = ROOT / "artifacts" / "case-eval" / run_id
    manifest = {
        "run_id": run_id,
        "command": COMMAND,
        "started_at": started.isoformat(),
        "git_commit": commit,
        "git_dirty": dirty,
        "scenario_set_version": scenarios.version,
        "truth_version": truth.version,
        "scenarios": len(scenarios.scenarios),
        "model": scripts.model_name,
        "prompt_version": PROMPT_VERSION,
        "retriever": "keyword_in_force",
        "reviewer": "scripted: accepts and approves as shown",
        "limitations": list(LIMITATIONS),
    }
    outcome = run_cases(runner, scenarios, truth, out, manifest)
    onramp_engine.dispose()
    marketplace_engine.dispose()

    print(f"run {run_id}  commit {commit[:8]}  dirty={dirty}  model={scripts.model_name}")
    for run in outcome.runs:
        grade = next((g for g in outcome.grades if g.scenario_id == run.scenario_id), None)
        verdict = "n/a" if grade is None else "correct" if grade.outcome_correct else "WRONG"
        print(
            f"  {run.scenario_id:42} {run.status.value:13} {run.final_state or 'n/a':17} {verdict}"
        )
    graded = outcome.summary["graded"]
    if graded:
        for key in (
            "outcome_accuracy",
            "resolution_macro_f1",
            "escalation_precision",
            "escalation_recall",
            "citation_precision",
            "citation_recall",
            "deterministic_fast_path_rate",
        ):
            print(f"  {key:42} {graded[key]:.3f}")
        print(f"  {'safety_violations':42} {graded['safety_violations']}")
    print(f"artifacts: {out.relative_to(ROOT)}")
    failures = gate_failures(outcome.summary, args.min_outcome_accuracy)
    if failures:
        sys.exit("gate failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
