import signal
import threading

from onramp.entrypoints.health import RoleHealth


def stop_on_signals(health: RoleHealth | None = None) -> threading.Event:
    """An event set by SIGTERM or SIGINT. Long-running roles check it between units of work,
    so a unit in progress always finishes before the process exits. The role stops reporting
    ready at once, while it drains."""
    stop = threading.Event()

    def handle(signum: int, _: object) -> None:
        if health is not None:
            health.mark_stopping()
        stop.set()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    return stop
