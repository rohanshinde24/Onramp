"""Replay a consumer group's parked messages: ``onramp-dlq-replay <topic> <consumer_group>``.

Run after fixing whatever made the consumer park them.
"""

import argparse
import sys

import onramp.models  # noqa: F401 - every table must be mapped before any session is used
from onramp.config import get_settings
from onramp.db.engine import make_engine, make_session_factory
from onramp.events.kafka import KafkaMessageSource, KafkaSink, consumer_config, producer_config
from onramp.events.replay import replay_parked
from onramp.events.topics import dlq
from onramp.telemetry.logging import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="onramp-dlq-replay")
    parser.add_argument("topic", help="source topic, e.g. marketplace.workers")
    parser.add_argument("group", help="consumer group whose parked messages to replay")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.kafka_bootstrap_servers:
        sys.exit("KAFKA_BOOTSTRAP_SERVERS is not set")

    bootstrap = settings.kafka_bootstrap_servers
    source = KafkaMessageSource(
        consumer_config(bootstrap, f"onramp-dlq-replay-{args.group}", "onramp-dlq-replay"),
        [dlq(args.topic)],
    )
    sink = KafkaSink(
        producer_config(bootstrap, "onramp-dlq-replay", settings.kafka_delivery_timeout_ms)
    )
    try:
        result = replay_parked(
            source,
            sink,
            make_session_factory(make_engine(str(settings.database_url))),
            group=args.group,
        )
    finally:
        source.close()
        sink.close()
    print(
        f"replayed={result.replayed} other_groups={result.skipped_other_group} "
        f"failed={result.failed}"
    )
    sys.exit(1 if result.failed else 0)


if __name__ == "__main__":
    main()
