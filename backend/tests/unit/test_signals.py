import os
import signal
from collections.abc import Iterator

import pytest

from onramp.entrypoints.signals import stop_on_signals


@pytest.fixture
def restore_handlers() -> Iterator[None]:
    previous = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    yield
    for sig, handler in previous.items():
        signal.signal(sig, handler)


@pytest.mark.usefixtures("restore_handlers")
def test_sigterm_sets_the_stop_event() -> None:
    stop = stop_on_signals()
    assert not stop.is_set()

    os.kill(os.getpid(), signal.SIGTERM)

    assert stop.wait(timeout=2)
