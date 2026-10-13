"""Process entrypoint for the onramp-outbox-publisher role."""

import logging
import sys
import threading
from datetime import UTC, datetime

from onramp.config import get_settings
from onramp.db.engine import make_engine, make_session_factory
from onramp.entrypoints.signals import stop_on_signals
from onramp.events.kafka import KafkaSink, producer_config
from onramp.events.publisher import OutboxPublisher, outbox_backlog
from onramp.telemetry.logging import configure_logging
from onramp.telemetry.tracing import configure_tracing

log = logging.getLogger("onramp.outbox_publisher")
BACKLOG_REPORT_INTERVAL_S = 15.0


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    provider = configure_tracing(settings, process_role="outbox-publisher")
    if not settings.kafka_bootstrap_servers:
        sys.exit("KAFKA_BOOTSTRAP_SERVERS is not set")

    engine = make_engine(str(settings.database_url))
    sessions = make_session_factory(engine)
    sink = KafkaSink(
        producer_config(
            settings.kafka_bootstrap_servers,
            f"{settings.kafka_client_id}-outbox-publisher",
            settings.kafka_delivery_timeout_ms,
        )
    )
    stop = stop_on_signals()

    def report_backlog() -> None:
        while not stop.wait(BACKLOG_REPORT_INTERVAL_S):
            try:
                with sessions() as session:
                    backlog = outbox_backlog(session, datetime.now(UTC))
                log.info(
                    "outbox backlog count=%d oldest_age_s=%.1f",
                    backlog.count,
                    backlog.oldest_age_s,
                )
            except Exception:
                log.exception("backlog report failed")

    reporter = threading.Thread(target=report_backlog, name="backlog-report", daemon=True)
    reporter.start()
    log.info("outbox publisher started")
    try:
        OutboxPublisher(sessions, sink).run_until_stopped(stop)
    finally:
        sink.close()
        engine.dispose()
        provider.shutdown()
        log.info("outbox publisher stopped")


if __name__ == "__main__":
    main()
