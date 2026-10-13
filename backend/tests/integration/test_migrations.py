import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, MetaData, inspect

import onramp.marketplace.models
import onramp.models
from onramp.db import migrate
from onramp.db.base import OnrampBase
from onramp.marketplace.models import MarketplaceBase

CHAINS = {
    "onramp": (OnrampBase.metadata, onramp.models),
    "marketplace": (MarketplaceBase.metadata, onramp.marketplace.models),
}


def _engine_and_url(chain: str, request: pytest.FixtureRequest) -> tuple[Engine, str]:
    engine: Engine = request.getfixturevalue(f"{chain}_engine")
    url: str = request.getfixturevalue(f"{chain}_db_url")
    return engine, url


@pytest.mark.parametrize("chain", CHAINS)
def test_models_match_migrations(chain: str, request: pytest.FixtureRequest) -> None:
    engine, _ = _engine_and_url(chain, request)
    metadata: MetaData = CHAINS[chain][0]
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        diff = compare_metadata(context, metadata)

    assert diff == [], f"{chain} models and migrations disagree; write a migration"


@pytest.mark.parametrize("chain", CHAINS)
def test_full_downgrade_and_upgrade(chain: str, request: pytest.FixtureRequest) -> None:
    engine, url = _engine_and_url(chain, request)
    expected = set(CHAINS[chain][0].tables)

    migrate.downgrade(chain, url, "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}

    migrate.upgrade(chain, url)
    assert expected <= set(inspect(engine).get_table_names())
