"""Engines and sessions.

Data access is synchronous SQLAlchemy over psycopg 3. FastAPI runs sync endpoints in its
threadpool, and Temporal activities can be sync, so one model of transactions serves every
process role.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# libpq options that turn a lost database into an error instead of a call that never returns.
# Without them a network partition blocks a query until the kernel gives up on the connection,
# many minutes later. A server that is up but not answering is still not caught here; a role's
# liveness probe sees its loop stop beating and restarts it.
CONNECT_ARGS: dict[str, int] = {
    "connect_timeout": 5,
    "keepalives": 1,
    "keepalives_idle": 10,
    "keepalives_interval": 5,
    "keepalives_count": 3,
    # Unacknowledged data for this long drops the connection (Linux; ignored elsewhere).
    "tcp_user_timeout": 30_000,
}


def make_engine(url: str) -> Engine:
    return create_engine(
        url, pool_pre_ping=True, pool_size=5, max_overflow=5, connect_args=CONNECT_ARGS
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@contextmanager
def transaction(factory: sessionmaker[Session]) -> Iterator[Session]:
    """One unit of work: commit on success, roll back on any exception."""
    with factory() as session, session.begin():
        yield session


def ping(engine: Engine) -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
