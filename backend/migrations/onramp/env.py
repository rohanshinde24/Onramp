from alembic import context
from sqlalchemy import create_engine

import onramp.models  # noqa: F401 - registers every table on the metadata
from onramp.db.base import OnrampBase


def _url() -> str:
    url = context.config.attributes.get("database_url")
    if url is None:
        from onramp.config import get_settings

        url = str(get_settings().database_url)
    return str(url)


def run() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=OnrampBase.metadata,
            compare_type=True,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run()
