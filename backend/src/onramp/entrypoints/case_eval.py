"""Run the whole-application case evaluation: ``onramp-case-eval``.

Recreates dedicated ``*_eval`` databases, runs every scenario in a suite (by default
``eval/cases/``) through the real case services with scripted investigations, grades each
against the suite's truth, and writes ``artifacts/<suite>/<run_id>/``. Exits non-zero when a
run fails the gate.

The evaluation package may not import the marketplace writer (ADR-008), so the services that
hold one are assembled here.
"""

import argparse
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

import onramp.models  # noqa: F401 - every table must be mapped before any session is used
from onramp.actions.evidence import EvidenceRequester
from onramp.actions.gateway import ActionGateway
from onramp.agent.anthropic_model import AnthropicChatModel
from onramp.agent.model import ChatModel
from onramp.agent.prompts import DEFAULT_PROMPT_VERSION, PROMPTS
from onramp.approvals.service import ApprovalService
from onramp.cases.evidence_consumer import CaseEvidenceHandler
from onramp.cases.service import CaseService
from onramp.checks.service import CheckService
from onramp.config import AppEnv, get_settings
from onramp.db import migrate
from onramp.db.engine import make_engine, make_session_factory
from onramp.evaluation.batching import AnthropicBatchSubmitter, record_investigations
from onramp.evaluation.faults import ScenarioFaults, load_faults, no_faults
from onramp.evaluation.recordings import RecordedModel, RecordingKey, RecordingStore
from onramp.evaluation.report import LIMITATIONS, gate_failures, git_state, run_cases
from onramp.evaluation.reviewers import honest_reviewers, load_reviewers
from onramp.evaluation.runner import CaseEvaluationRunner
from onramp.evaluation.scenarios import ScenarioSet, load_scenarios, validate_truth
from onramp.evaluation.scripted import KeywordPolicyRetriever, ScriptSet, load_scripts
from onramp.evaluation.services import CaseServices, VirtualClock
from onramp.evaluation.spend import PRICES, SpendCapReachedError, SpendMeter
from onramp.evaluation.worlds import reset_databases
from onramp.investigations.service import InvestigationService
from onramp.marketplace.faults import FaultyMarketplaceWriter
from onramp.marketplace.reader import SqlMarketplaceReader
from onramp.marketplace.writer import MarketplaceWriter, SqlMarketplaceWriter
from onramp.policy.corpus import load_corpus
from onramp.policy.search import PolicyRetriever
from onramp.proposals.service import ProposalService
from onramp.workflows.activities import AgentActivities, WorkflowActivities


def scenario_writer(
    marketplace: sessionmaker[Session], clock: VirtualClock, faults: ScenarioFaults
) -> MarketplaceWriter:
    """The marketplace writer for one scenario, wrapped with its injected faults if any."""
    writer = SqlMarketplaceWriter(marketplace, clock)
    plan = faults.marketplace_plan()
    return FaultyMarketplaceWriter(writer, plan) if plan else writer


def build_services(
    onramp: sessionmaker[Session],
    marketplace: sessionmaker[Session],
    retriever: PolicyRetriever,
    clock: VirtualClock,
    model: ChatModel,
    writer: MarketplaceWriter | None = None,
    gateway: type[ActionGateway] = ActionGateway,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> CaseServices:
    reader = SqlMarketplaceReader(marketplace)
    writer = writer or SqlMarketplaceWriter(marketplace, clock)
    checks = CheckService(onramp, reader, EvidenceRequester(writer), clock)
    investigations = InvestigationService(
        onramp, reader, retriever, model, clock=clock, prompt_version=prompt_version
    )
    action_gateway = gateway(onramp, reader, writer, clock)
    return CaseServices(
        cases=CaseService(onramp, reader, clock),
        workflow=WorkflowActivities(onramp, checks, action_gateway, clock),
        gateway=action_gateway,
        evidence=CaseEvidenceHandler(reader, clock),
        agent=AgentActivities(onramp, investigations),
        proposals=ProposalService(onramp, clock),
        approvals=ApprovalService(onramp, clock),
    )


ROOT = Path(__file__).resolve().parents[4]
CASES = ROOT / "eval" / "cases"


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


def _live_models(args: argparse.Namespace, api_key: str) -> tuple[Callable[[str], ChatModel], str]:
    """Recorded answers where they still match; the live model, built on first need, for the
    rest."""
    store = RecordingStore(args.recordings)
    name = f"{args.model}@{args.effort}"
    live: list[ChatModel] = []

    def provider() -> ChatModel:
        if not live:
            import anthropic  # optional dependency, needed only for live calls

            client = anthropic.Anthropic(api_key=api_key, timeout=120.0)
            live.append(AnthropicChatModel(client, model=args.model, effort=args.effort))
        return live[0]

    def model_for(scenario_id: str) -> ChatModel:
        key = RecordingKey(name, args.prompt, scenario_id, args.repeat)
        return RecordedModel(key, store, provider)

    return model_for, name


def select_models(
    args: argparse.Namespace, api_key: SecretStr | None, scripts: ScriptSet
) -> tuple[Callable[[str], ChatModel], str]:
    """The scripted investigators, or with ``--model`` the live model behind recordings."""
    if not args.model:
        if args.batch:
            sys.exit("--batch records live answers: it needs --model and --max-cost")
        return scripts.model_for, scripts.model_name
    if args.max_cost is None or args.max_cost <= 0:
        sys.exit("--model costs money: set --max-cost to a positive dollar amount")
    if api_key is None:
        sys.exit("ANTHROPIC_API_KEY is not set in the environment or .env")
    return _live_models(args, api_key.get_secret_value())


def _record_in_batches(
    args: argparse.Namespace,
    api_key: str,
    runner: CaseEvaluationRunner,
    scenarios: ScenarioSet,
    meter: SpendMeter,
) -> dict[str, object]:
    """Phase one of a batch run: snapshot each case at the investigator and record every
    investigation through batches. The normal run that follows replays them."""
    import anthropic  # optional dependency, needed only for live calls

    client = anthropic.Anthropic(api_key=api_key, timeout=120.0)
    model = AnthropicChatModel(client, model=args.model, effort=args.effort)
    snapshots = [snap for s in scenarios.scenarios if (snap := runner.snapshot(s)) is not None]
    print(f"batch: {len(snapshots)} of {len(scenarios.scenarios)} scenarios need the investigator")
    submitter = AnthropicBatchSubmitter(client)
    summary: dict[str, object] = {
        "investigations": len(snapshots),
        "batch_ids": submitter.batch_ids,
    }
    try:
        result = record_investigations(
            snapshots,
            model=model,
            prompt_version=args.prompt,
            store=RecordingStore(args.recordings),
            meter=meter,
            submitter=submitter,
            repeat=args.repeat,
        )
    except SpendCapReachedError as exc:
        summary["stopped"] = str(exc)
        return summary
    summary |= {
        "batches": result.batches,
        "requests": result.requests,
        "not_fully_recorded": result.failures,
    }
    print(f"batch: {result.requests} requests in {result.batches} batches")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenarios", type=Path, default=CASES / "scenarios.yaml")
    parser.add_argument("--truth", type=Path, default=CASES / "truth.yaml")
    parser.add_argument("--scripts", type=Path, default=CASES / "investigations.yaml")
    parser.add_argument(
        "--reviewers", type=Path, help="scenario -> reviewer behavior; honest when omitted"
    )
    parser.add_argument(
        "--faults", type=Path, help="scenario -> injected faults; none when omitted"
    )
    parser.add_argument("--prompt", choices=sorted(PROMPTS), default=DEFAULT_PROMPT_VERSION)
    parser.add_argument(
        "--model",
        choices=sorted(PRICES),
        help="investigate with this live model instead of the scripts; costs money",
    )
    parser.add_argument("--effort", choices=["low", "medium", "high"], default="medium")
    parser.add_argument(
        "--recordings",
        type=Path,
        default=ROOT / "artifacts" / "recordings",
        help="recorded live answers; matching ones are replayed instead of paid for again",
    )
    parser.add_argument("--repeat", type=int, default=0, help="which repeat of a live run this is")
    parser.add_argument(
        "--batch",
        action="store_true",
        help="record investigations through the Message Batches API first (half price), then"
        " replay them; needs --model",
    )
    parser.add_argument(
        "--max-cost",
        type=Decimal,
        help="US dollars this run may spend on the model; required with --model",
    )
    parser.add_argument("--min-outcome-accuracy", type=float, default=1.0)
    parser.add_argument(
        "--suite",
        default="case-eval",
        help="names the artifact directory and the make target that reproduces the run",
    )
    args = parser.parse_args()

    settings = get_settings()
    if settings.app_env is AppEnv.STAGING:
        sys.exit("onramp-case-eval recreates databases and does not run in staging")
    scenarios, truth = load_scenarios(args.scenarios, args.truth)
    corpus = load_corpus(ROOT / "policies")
    validate_truth(truth, corpus)
    scripts = load_scripts(args.scripts)
    models, model_name = select_models(args, settings.anthropic_api_key, scripts)
    meter = SpendMeter(cap_usd=args.max_cost)

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
        lambda clock, model, faults: build_services(
            onramp,
            marketplace,
            faults.wrap_retriever(retriever),
            clock,
            model,
            scenario_writer(marketplace, clock, faults),
            prompt_version=args.prompt,
        ),
        corpus,
        models,
        reviewers=load_reviewers(args.reviewers) if args.reviewers else honest_reviewers,
        faults=load_faults(args.faults) if args.faults else no_faults,
        meter=meter,
    )

    started = datetime.now(UTC)
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    commit, dirty = git_state(ROOT)
    out = ROOT / "artifacts" / args.suite / run_id
    manifest = {
        "run_id": run_id,
        "command": f"make {args.suite}",
        "dataset": {
            name: str(path.resolve().relative_to(ROOT))
            for name, path in (
                ("scenarios", args.scenarios),
                ("truth", args.truth),
                ("scripts", args.scripts),
                ("reviewers", args.reviewers),
                ("faults", args.faults),
            )
            if path is not None
        },
        "started_at": started.isoformat(),
        "git_commit": commit,
        "git_dirty": dirty,
        "scenario_set_version": scenarios.version,
        "truth_version": truth.version,
        "scenarios": len(scenarios.scenarios),
        "model": model_name,
        "spend_cap_usd": str(args.max_cost) if args.max_cost else None,
        "recordings": str(args.recordings.resolve().relative_to(ROOT)) if args.model else None,
        "repeat": args.repeat if args.model else None,
        "prompt_version": args.prompt,
        "retriever": "keyword_in_force",
        "reviewer": "per scenario" if args.reviewers else "honest: accepts and approves as shown",
        "limitations": list(LIMITATIONS),
    }
    if args.batch and settings.anthropic_api_key is not None:
        manifest["batch"] = _record_in_batches(
            args, settings.anthropic_api_key.get_secret_value(), runner, scenarios, meter
        )
    outcome = run_cases(runner, scenarios, truth, out, manifest)
    onramp_engine.dispose()
    marketplace_engine.dispose()

    print(f"run {run_id}  commit {commit[:8]}  dirty={dirty}  model={model_name}")
    print(
        f"  paid ${outcome.summary['paid_this_run_usd']:.4f} for {meter.calls} live model calls;"
        f" answers cost ${outcome.summary['cost_usd']:.4f} in total including replays"
    )
    for run in outcome.runs:
        grade = next((g for g in outcome.grades if g.scenario_id == run.scenario_id), None)
        verdict = "n/a" if grade is None else "correct" if grade.outcome_correct else "WRONG"
        print(
            f"  {run.scenario_id:50} {run.status.value:13} {run.final_state or 'n/a':17} {verdict}"
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
            print(f"  {key:50} {graded[key]:.3f}")
        print(f"  {'safety_violations':50} {graded['safety_violations']}")
    print(f"artifacts: {out.relative_to(ROOT)}")
    failures = gate_failures(outcome.summary, args.min_outcome_accuracy)
    if failures:
        sys.exit("gate failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
