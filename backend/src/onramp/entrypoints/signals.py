import signal
import threading


def stop_on_signals() -> threading.Event:
    """An event set by SIGTERM or SIGINT. Long-running roles check it between units of work,
    so a unit in progress always finishes before the process exits."""
    stop = threading.Event()

    def handle(signum: int, _: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    return stop
