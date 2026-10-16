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
from onramp.telemetry.metrics import consumer_lag, consumer_lag_age
from onramp.telemetry.setup import configure_telemetry, instrument_databases
from onramp.workflows.client import TemporalCaseCoordinator

log = logging.getLogger("onramp.event_consumer")
REPORT_INTERVAL_S = 15.0


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    telemetry = configure_telemetry(settings, process_role="event-consumer")
    bootstrap = settings.kafka_bootstrap_servers
    if not bootstrap:
        sys.exit("KAFKA_BOOTSTRAP_SERVERS is not set")

    engine = make_engine(str(settings.database_url))
    marketplace_engine = make_engine(str(settings.marketplace_database_url))
    instrument_databases(engine, marketplace_engine)
    coordinator = TemporalCaseCoordinator(
        settings.temporal_address,
        settings.temporal_namespace,
        settings.temporal_workflow_task_queue,
        settings.temporal_agent_task_queue,
    )
    handler = CaseEvidenceHandler(
        SqlMarketplaceReader(make_session_factory(marketplace_engine)), coordinator=coordinator
    )
    client_id = f"{settings.kafka_client_id}-{handler.group}"
    source = KafkaMessageSource(
        consumer_config(bootstrap, handler.group, client_id), handler.topics
    )
    dead_letters = KafkaDeadLetterSink(
        producer_config(bootstrap, f"{client_id}-dlq", settings.kafka_delivery_timeout_ms)
    )
    runner = ConsumerRunner(make_session_factory(engine), source, dead_letters, handler)
    consumer_lag_age.mark({"group": handler.group})
    stop = stop_on_signals()

    def report() -> None:
        counts = " ".join(f"{k.value}={v}" for k, v in runner.stats.counts.items())
        try:
            lag = source.lag()
        except Exception:
            consumer_lag.clear({"group": handler.group})
            raise
        consumer_lag.set(lag, {"group": handler.group})
        consumer_lag_age.mark({"group": handler.group})
        log.info("consumer group=%s lag=%d %s", handler.group, lag, counts)

    log.info("event consumer started group=%s topics=%s", handler.group, ",".join(handler.topics))
    try:
        runner.run_until_stopped(stop, on_tick=report, tick_interval_s=REPORT_INTERVAL_S)
    finally:
        coordinator.close()
        engine.dispose()
        marketplace_engine.dispose()
        telemetry.shutdown()
        log.info("event consumer stopped")


if __name__ == "__main__":
    main()
