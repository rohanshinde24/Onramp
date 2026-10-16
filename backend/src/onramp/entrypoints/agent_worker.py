"""Temporal agent worker: bounded model investigations on an isolated task queue."""

import asyncio
import logging
import os
import sys

from elasticsearch import Elasticsearch

import onramp.models  # noqa: F401 - configure every ORM mapper before the first query
from onramp.agent.anthropic_model import AnthropicChatModel
from onramp.config import get_settings
from onramp.db.engine import make_engine, make_session_factory
from onramp.entrypoints.health import HealthEndpoint
from onramp.entrypoints.temporal_worker import run_worker
from onramp.investigations.service import InvestigationService
from onramp.marketplace.reader import SqlMarketplaceReader
from onramp.policy.embeddings import FastEmbedder
from onramp.policy.search import default_retriever
from onramp.telemetry.logging import configure_logging
from onramp.telemetry.setup import configure_telemetry, instrument_databases
from onramp.workflows.activities import AgentActivities

log = logging.getLogger("onramp.agent_worker")


def main() -> None:
    settings = get_settings()
    if not settings.elasticsearch_url:
        sys.exit("ELASTICSEARCH_URL is not set")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set; the agent worker needs model credentials")
    configure_logging(settings.log_level)
    telemetry = configure_telemetry(settings, process_role="agent-worker")
    engine = make_engine(str(settings.database_url))
    marketplace_engine = make_engine(str(settings.marketplace_database_url))
    instrument_databases(engine, marketplace_engine)
    sessions = make_session_factory(engine)
    marketplace_sessions = make_session_factory(marketplace_engine)
    search = Elasticsearch(settings.elasticsearch_url)
    activities = AgentActivities(
        sessions,
        InvestigationService(
            sessions,
            SqlMarketplaceReader(marketplace_sessions),
            default_retriever(search, settings.policy_index_alias, FastEmbedder()),
            AnthropicChatModel(),
        ),
    )
    log.info("agent worker started queue=%s", settings.temporal_agent_task_queue)
    try:
        with HealthEndpoint(settings.health_port) as health:
            asyncio.run(
                run_worker(
                    address=settings.temporal_address,
                    namespace=settings.temporal_namespace,
                    otlp_endpoint=settings.otel_exporter_otlp_endpoint,
                    task_queue=settings.temporal_agent_task_queue,
                    activities=[activities.run_investigation],
                    health=health,
                )
            )
    finally:
        log.info("agent worker stopped")
        search.close()
        engine.dispose()
        marketplace_engine.dispose()
        telemetry.shutdown()


if __name__ == "__main__":
    main()
