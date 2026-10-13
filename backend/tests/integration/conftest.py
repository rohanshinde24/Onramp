"""Fixtures for tests that need real PostgreSQL.

Each test session creates fresh databases named after the configured ones with a `_test`
suffix and migrates them to head. Each test runs inside a transaction that is rolled back, so
tests do not see each other's rows.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from onramp.config import get_settings
from onramp.db import migrate


def _recreate_database(url: str) -> str:
    target = make_url(url)
    test_name = f"{target.database}_test"
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{test_name}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{test_name}"'))
    admin.dispose()
    return target.set(database=test_name).render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def onramp_db_url() -> str:
    url = _recreate_database(str(get_settings().database_url))
    migrate.upgrade("onramp", url)
    return url


@pytest.fixture(scope="session")
def onramp_engine(onramp_db_url: str) -> Iterator[Engine]:
    engine = create_engine(onramp_db_url)
    yield engine
    engine.dispose()


@pytest.fixture
def onramp_session(onramp_engine: Engine) -> Iterator[Session]:
    with onramp_engine.connect() as connection:
        outer = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            outer.rollback()


@pytest.fixture(scope="session")
def marketplace_db_url() -> str:
    url = _recreate_database(str(get_settings().marketplace_database_url))
    migrate.upgrade("marketplace", url)
    return url


@pytest.fixture(scope="session")
def marketplace_engine(marketplace_db_url: str) -> Iterator[Engine]:
    engine = create_engine(marketplace_db_url)
    yield engine
    engine.dispose()


@pytest.fixture
def marketplace_session(marketplace_engine: Engine) -> Iterator[Session]:
    with marketplace_engine.connect() as connection:
        outer = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            outer.rollback()
