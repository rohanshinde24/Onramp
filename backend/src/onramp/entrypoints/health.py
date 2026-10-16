"""Liveness and readiness for process roles that do not serve HTTP.

The API answers probes itself. The publishers, consumers and Temporal workers run a loop
instead, so each one beats a ``RoleHealth`` from that loop and, when ``HEALTH_PORT`` is set,
serves it on a small HTTP server for Kubernetes probes:

* ``/healthz`` fails only when the loop has stopped beating, which means the process is wedged
  and a restart helps. A dependency outage does not stop the loop (failures are logged and the
  loop goes round again), so an outage never gets a pod restarted.
* ``/readyz`` succeeds once the role has finished starting and until it begins to shut down.
  Nothing routes traffic to these roles; readiness is what a rolling update waits for before it
  stops an old pod.

Before the role is ready the loop has not started, so liveness holds; the startup probe, which
reads readiness, bounds how long starting may take.
"""

import logging
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("onramp.health")


class RoleHealth:
    def __init__(
        self, *, stale_after_s: float = 120.0, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self._stale_after_s = stale_after_s
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._last_beat = monotonic()
        self._ready = False
        self._stopping = False

    def beat(self) -> None:
        with self._lock:
            self._last_beat = self._monotonic()

    def mark_ready(self) -> None:
        with self._lock:
            self._ready = True
            self._last_beat = self._monotonic()

    def mark_stopping(self) -> None:
        with self._lock:
            self._stopping = True

    def live(self) -> bool:
        with self._lock:
            if not self._ready:
                return True
            return self._monotonic() - self._last_beat < self._stale_after_s

    def ready(self) -> bool:
        with self._lock:
            ready = self._ready and not self._stopping
        return ready and self.live()


def serve_health(health: RoleHealth, port: int, host: str = "0.0.0.0") -> ThreadingHTTPServer:  # noqa: S104 - probe port inside the pod
    """Serve ``/healthz`` and ``/readyz`` from a daemon thread. The caller shuts it down."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/healthz":
                ok = health.live()
            elif self.path == "/readyz":
                ok = health.ready()
            else:
                self.send_error(404)
                return
            body = b"ok\n" if ok else b"unavailable\n"
            self.send_response(200 if ok else 503)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return  # probes every few seconds would drown the role's own logs

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="health", daemon=True)
    thread.start()
    log.info("health server listening port=%d", server.server_address[1])
    return server


class HealthEndpoint:
    """A role's health, served on ``port`` when one is configured. Use as a context manager so
    the server stops with the role."""

    def __init__(self, port: int | None, *, stale_after_s: float = 120.0) -> None:
        self.health = RoleHealth(stale_after_s=stale_after_s)
        self._port = port
        self._server: ThreadingHTTPServer | None = None

    def __enter__(self) -> RoleHealth:
        if self._port is not None:
            self._server = serve_health(self.health, self._port)
        return self.health

    def __exit__(self, *_: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
