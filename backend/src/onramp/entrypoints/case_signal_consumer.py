"""Kafka fallback for starting and waking case workflows from durable domain events."""

import logging
import sys

import onramp.models  # noqa: F401 - configure every ORM mapper before the first query
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
from onramp.telemetry.logging import configure_logging
from onramp.telemetry.metrics import consumer_lag
from onramp.telemetry.setup import configure_telemetry, instrument_databases
from onramp.workflows.client import TemporalCaseCoordinator
from onramp.workflows.event_handler import CaseSignalHandler

log = logging.getLogger("onramp.case_signal_consumer")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    telemetry = configure_telemetry(settings, process_role="case-signal-consumer")
    bootstrap = settings.kafka_bootstrap_servers
    if not bootstrap:
        sys.exit("KAFKA_BOOTSTRAP_SERVERS is not set")
    engine = make_engine(str(settings.database_url))
    instrument_databases(engine)
    coordinator = TemporalCaseCoordinator(
        settings.temporal_address,
        settings.temporal_namespace,
        settings.temporal_workflow_task_queue,
        settings.temporal_agent_task_queue,
    )
    handler = CaseSignalHandler(coordinator)
    client_id = f"{settings.kafka_client_id}-{handler.group}"
    source = KafkaMessageSource(
        consumer_config(bootstrap, handler.group, client_id), handler.topics
    )
    dead_letters = KafkaDeadLetterSink(
        producer_config(bootstrap, f"{client_id}-dlq", settings.kafka_delivery_timeout_ms)
    )
    runner = ConsumerRunner(make_session_factory(engine), source, dead_letters, handler)

    def report() -> None:
        try:
            lag = source.lag()
        except Exception:
            consumer_lag.clear({"group": handler.group})
            raise
        consumer_lag.set(lag, {"group": handler.group})

    try:
        runner.run_until_stopped(stop_on_signals(), on_tick=report)
    finally:
        coordinator.close()
        engine.dispose()
        telemetry.shutdown()


if __name__ == "__main__":
    main()
