"""Process entrypoint for the onramp-event-consumer role (the case evidence consumer)."""

import logging
import sys

import onramp.models  # noqa: F401 - every table must be mapped before any session is used
from onramp.cases.evidence_consumer import CaseEvidenceHandler
from onramp.config import get_settings
from onramp.db.engine import make_engine, make_session_factory
from onramp.entrypoints.signals import stop_on_signals
from onramp.events.consumer import ConsumerRunner
from onramp.events.kafka import (
    KafkaDeadLetterSink,
    KafkaMessageSource,
    consumer_config,
    producer_config,
)
from onramp.marketplace.reader import SqlMarketplaceReader
from onramp.telemetry.logging import configure_logging
from onramp.telemetry.tracing import configure_tracing

log = logging.getLogger("onramp.event_consumer")
REPORT_INTERVAL_S = 15.0


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    provider = configure_tracing(settings, process_role="event-consumer")
    bootstrap = settings.kafka_bootstrap_servers
    if not bootstrap:
        sys.exit("KAFKA_BOOTSTRAP_SERVERS is not set")

    engine = make_engine(str(settings.database_url))
    marketplace_engine = make_engine(str(settings.marketplace_database_url))
    handler = CaseEvidenceHandler(SqlMarketplaceReader(make_session_factory(marketplace_engine)))
    client_id = f"{settings.kafka_client_id}-{handler.group}"
    source = KafkaMessageSource(
        consumer_config(bootstrap, handler.group, client_id), handler.topics
    )
    dead_letters = KafkaDeadLetterSink(
        producer_config(bootstrap, f"{client_id}-dlq", settings.kafka_delivery_timeout_ms)
    )
    runner = ConsumerRunner(make_session_factory(engine), source, dead_letters, handler)
    stop = stop_on_signals()

    def report() -> None:
        counts = " ".join(f"{k.value}={v}" for k, v in runner.stats.counts.items())
        log.info("consumer group=%s lag=%d %s", handler.group, source.lag(), counts)

    log.info("event consumer started group=%s topics=%s", handler.group, ",".join(handler.topics))
    try:
        runner.run_until_stopped(stop, on_tick=report, tick_interval_s=REPORT_INTERVAL_S)
    finally:
        engine.dispose()
        marketplace_engine.dispose()
        provider.shutdown()
        log.info("event consumer stopped")


if __name__ == "__main__":
    main()
