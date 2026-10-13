from datetime import datetime
from functools import partial
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from onramp.cases.service import CaseService, IntakeRequest
from onramp.db.base import OnrampBase
from onramp.evaluation.scenarios import load_scenarios
from onramp.evaluation.worlds import (
    SERVICE,
    TENANT,
    WORLDS,
    UnknownWorldError,
    UnsafeResetError,
    build_world,
    reset_databases,
)
from onramp.identity.principal import Principal
from onramp.identity.roles import Role
from onramp.marketplace.models import MarketplaceBase, Worker
from onramp.marketplace.reader import SqlMarketplaceReader
from onramp.policy.corpus import load_corpus
from onramp.policy.models import PolicyVersion

ROOT = Path(__file__).resolve().parents[3]
CORPUS = load_corpus(ROOT / "policies")
Sessions = sessionmaker[Session]


def _fixed(at: datetime) -> datetime:
    return at


def _reset(onramp_engine: Engine, marketplace_engine: Engine) -> None:
    reset_databases(
        [(onramp_engine, OnrampBase.metadata), (marketplace_engine, MarketplaceBase.metadata)]
    )


def test_every_committed_scenario_opens_a_case_in_its_own_world(
    onramp_engine: Engine,
    marketplace_engine: Engine,
    onramp_committed: Sessions,
    marketplace_committed: Sessions,
) -> None:
    scenarios, _ = load_scenarios(
        ROOT / "eval" / "cases" / "scenarios.yaml", ROOT / "eval" / "cases" / "truth.yaml"
    )
    service = Principal(TENANT, SERVICE, Role.MARKETPLACE_SERVICE)
    for scenario in scenarios.scenarios:
        _reset(onramp_engine, marketplace_engine)
        world = build_world(scenario.world_fixture, onramp_committed, marketplace_committed, CORPUS)
        cases = CaseService(
            onramp_committed,
            SqlMarketplaceReader(marketplace_committed),
            partial(_fixed, world.opened_at),
        )

        result = cases.open_case(
            service,
            IntakeRequest(
                case_type=scenario.case_type,
                subject_id=scenario.subject_id,
                intake_key=f"eval-{scenario.id}",
                description=scenario.report,
            ),
        )

        assert result.created, scenario.id


def test_reset_leaves_nothing_from_the_previous_world(
    onramp_engine: Engine,
    marketplace_engine: Engine,
    onramp_committed: Sessions,
    marketplace_committed: Sessions,
) -> None:
    _reset(onramp_engine, marketplace_engine)
    build_world("onboarding_requirements_met", onramp_committed, marketplace_committed, CORPUS)
    _reset(onramp_engine, marketplace_engine)

    build_world("onboarding_identity_failed", onramp_committed, marketplace_committed, CORPUS)

    with marketplace_committed() as session:
        assert session.get(Worker, "w_ana") is None
        assert session.get(Worker, "w_ben") is not None
    with onramp_committed() as session:
        assert session.scalar(select(func.count()).select_from(PolicyVersion)) == len(CORPUS)


def test_reset_refuses_a_database_that_is_not_for_evaluation(onramp_db_url: str) -> None:
    engine = create_engine(make_url(onramp_db_url).set(database="onramp"))
    try:
        with pytest.raises(UnsafeResetError):
            reset_databases([(engine, OnrampBase.metadata)])
    finally:
        engine.dispose()


def test_unknown_world_is_an_error(
    onramp_committed: Sessions, marketplace_committed: Sessions
) -> None:
    assert "no_such_world" not in WORLDS
    with pytest.raises(UnknownWorldError):
        build_world("no_such_world", onramp_committed, marketplace_committed, CORPUS)
