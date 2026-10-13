"""Create the Kafka topics Onramp needs: ``onramp-kafka-topics``. Safe to run repeatedly."""

import sys

from onramp.config import get_settings
from onramp.events.kafka import ensure_topics
from onramp.events.topics import all_topic_names


def main() -> None:
    settings = get_settings()
    if not settings.kafka_bootstrap_servers:
        sys.exit("KAFKA_BOOTSTRAP_SERVERS is not set")
    created = ensure_topics(
        settings.kafka_bootstrap_servers,
        all_topic_names(),
        partitions=settings.kafka_topic_partitions,
        replication_factor=settings.kafka_replication_factor,
    )
    print(f"created: {', '.join(created) or 'none (all present)'}")


if __name__ == "__main__":
    main()
