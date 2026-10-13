"""Builds a case-evaluation runner over the integration test databases."""

from collections.abc import Callable
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from onramp.actions.gateway import ActionGateway
from onramp.agent.model import ChatModel
from onramp.entrypoints.case_eval import build_services
from onramp.evaluation.runner import CaseEvaluationRunner, CaseServices, VirtualClock
from onramp.evaluation.scripted import KeywordPolicyRetriever, ScriptSet
from onramp.evaluation.worlds import reset_databases
from onramp.marketplace.writer import MarketplaceWriter
from onramp.policy.corpus import load_corpus

ROOT = Path(__file__).resolve().parents[3]
CORPUS = load_corpus(ROOT / "policies")
Sessions = sessionmaker[Session]
WriterFactory = Callable[[Sessions, VirtualClock], MarketplaceWriter]


def make_runner(
    engines: tuple[Engine, Engine],
    onramp: Sessions,
    marketplace: Sessions,
    scripts: ScriptSet,
    writer: WriterFactory | None = None,
    gateway: type[ActionGateway] = ActionGateway,
) -> CaseEvaluationRunner:
    def services(clock: VirtualClock, model: ChatModel) -> CaseServices:
        return build_services(
            onramp,
            marketplace,
            KeywordPolicyRetriever(onramp),
            clock,
            model,
            writer(marketplace, clock) if writer else None,
            gateway,
        )

    return CaseEvaluationRunner(
        onramp,
        marketplace,
        lambda: reset_databases(*engines),
        services,
        CORPUS,
        scripts.model_for,
    )
