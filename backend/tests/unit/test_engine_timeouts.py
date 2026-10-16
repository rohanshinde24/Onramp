import socket
import time

import pytest
from sqlalchemy.exc import OperationalError

from onramp.db.engine import make_engine, ping


def test_unreachable_database_fails_within_the_connect_timeout() -> None:
    # A listening socket that never answers the PostgreSQL handshake stands in for a database
    # whose host stopped responding.
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        port = silent.getsockname()[1]
        engine = make_engine(f"postgresql+psycopg://onramp:x@127.0.0.1:{port}/onramp")
        started = time.monotonic()
        with pytest.raises(OperationalError):
            ping(engine)
        elapsed = time.monotonic() - started
        engine.dispose()

    assert elapsed < 15
