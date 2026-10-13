from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect

import onramp.models  # noqa: F401 - registers every table
from onramp.db import migrate
from onramp.db.base import OnrampBase


def test_models_match_migrations(onramp_engine: Engine) -> None:
    with onramp_engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        diff = compare_metadata(context, OnrampBase.metadata)

    assert diff == [], "models and migrations disagree; write a migration"


def test_full_downgrade_and_upgrade(onramp_db_url: str, onramp_engine: Engine) -> None:
    migrate.downgrade("onramp", onramp_db_url, "base")
    assert set(inspect(onramp_engine).get_table_names()) == {"alembic_version"}

    migrate.upgrade("onramp", onramp_db_url)
    assert {"tenant", "app_user"} <= set(inspect(onramp_engine).get_table_names())
