from alembic import context
from sqlalchemy import create_engine

import onramp.marketplace.models  # noqa: F401 - registers every table on the metadata
from onramp.marketplace.models import MarketplaceBase


def _url() -> str:
    url = context.config.attributes.get("database_url")
    if url is None:
        from onramp.config import get_settings

        url = str(get_settings().marketplace_database_url)
    return str(url)


def run() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=MarketplaceBase.metadata,
            compare_type=True,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run()
