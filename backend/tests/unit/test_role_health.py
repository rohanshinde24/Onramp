import asyncio
import os
import signal
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any, cast

import pytest

from onramp.entrypoints.health import HealthEndpoint, RoleHealth, serve_health
from onramp.entrypoints.signals import stop_on_signals
from onramp.entrypoints.temporal_worker import _beat_while
from onramp.events.consumer import ConsumerRunner
from onramp.events.publisher import OutboxPublisher


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_starting_role_is_live_but_not_ready() -> None:
    clock = Clock()
    health = RoleHealth(stale_after_s=10, monotonic=clock)
    clock.now += 3600  # a slow start is the startup probe's business, not liveness

    assert health.live()
    assert not health.ready()


def test_ready_role_stays_live_while_it_beats() -> None:
    clock = Clock()
    health = RoleHealth(stale_after_s=10, monotonic=clock)
    health.mark_ready()
    for _ in range(5):
        clock.now += 9
        health.beat()

    assert health.live()
    assert health.ready()


def test_role_that_stops_beating_fails_liveness_and_readiness() -> None:
    clock = Clock()
    health = RoleHealth(stale_after_s=10, monotonic=clock)
    health.mark_ready()
    clock.now += 10

    assert not health.live()
    assert not health.ready()


def test_stopping_role_is_not_ready_but_still_live() -> None:
    health = RoleHealth()
    health.mark_ready()
    health.mark_stopping()

    assert health.live()
    assert not health.ready()


def _get(port: int, path: str) -> int:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
            return cast(int, response.status)
    except urllib.error.HTTPError as exc:
        return exc.code


def test_probe_endpoints_report_health() -> None:
    health = RoleHealth()
    server = serve_health(health, port=0, host="127.0.0.1")
    port = server.server_address[1]
    try:
        assert _get(port, "/healthz") == 200
        assert _get(port, "/readyz") == 503
        health.mark_ready()
        assert _get(port, "/readyz") == 200
        health.mark_stopping()
        assert _get(port, "/readyz") == 503
        assert _get(port, "/other") == 404
    finally:
        server.shutdown()
        server.server_close()


def test_endpoint_without_a_port_serves_nothing() -> None:
    with HealthEndpoint(None) as health:
        health.mark_ready()
        assert health.ready()


@pytest.fixture
def restore_handlers() -> Iterator[None]:
    previous = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    yield
    for sig, handler in previous.items():
        signal.signal(sig, handler)


@pytest.mark.usefixtures("restore_handlers")
def test_sigterm_withdraws_readiness_before_the_role_stops() -> None:
    health = RoleHealth()
    health.mark_ready()
    stop = stop_on_signals(health)

    os.kill(os.getpid(), signal.SIGTERM)

    assert stop.wait(timeout=2)
    assert not health.ready()


class IdleSource:
    def __init__(self, stop: threading.Event, polls: int) -> None:
        self._stop = stop
        self._polls = polls

    def poll(self, timeout_s: float) -> None:
        self._polls -= 1
        if self._polls == 0:
            self._stop.set()

    def close(self) -> None:
        return None


def test_consumer_loop_beats_on_every_poll() -> None:
    stop = threading.Event()
    beats: list[None] = []
    runner = ConsumerRunner(
        cast(Any, None), cast(Any, IdleSource(stop, polls=3)), cast(Any, None), cast(Any, None)
    )

    runner.run_until_stopped(stop, heartbeat=lambda: beats.append(None))

    assert len(beats) == 3


class FailingPublisher(OutboxPublisher):
    def __init__(self, stop: threading.Event) -> None:
        self._stop = stop
        self._batch_size = 1
        self.cycles = 0

    def run_once(self) -> Any:
        self.cycles += 1
        if self.cycles == 3:
            self._stop.set()
        raise ConnectionError("broker down")


def test_publisher_keeps_beating_while_cycles_fail() -> None:
    stop = threading.Event()
    beats: list[None] = []
    publisher = FailingPublisher(stop)

    publisher.run_until_stopped(stop, idle_wait_s=0, heartbeat=lambda: beats.append(None))

    assert len(beats) == 3


def test_temporal_worker_heartbeat_marks_ready_and_stops_with_the_worker() -> None:
    health = RoleHealth()

    async def scenario() -> None:
        running = asyncio.create_task(asyncio.sleep(0.05))
        await _beat_while(running, health)
        assert running.done()

    asyncio.run(scenario())

    assert health.ready()
