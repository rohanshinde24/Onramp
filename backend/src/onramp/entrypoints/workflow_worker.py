"""Temporal workflow worker: deterministic orchestration and non-model activities."""

import asyncio
import logging

import onramp.models  # noqa: F401 - configure every ORM mapper before the first query
from onramp.actions.evidence import EvidenceRequester
from onramp.actions.gateway import ActionGateway
from onramp.checks.service import CheckService
from onramp.config import get_settings
from onramp.db.engine import make_engine, make_session_factory
from onramp.entrypoints.temporal_worker import run_worker
from onramp.marketplace.reader import SqlMarketplaceReader
from onramp.marketplace.writer import SqlMarketplaceWriter
from onramp.telemetry.logging import configure_logging
from onramp.telemetry.tracing import configure_tracing
from onramp.workflows.activities import WorkflowActivities
from onramp.workflows.case import CaseWorkflow

log = logging.getLogger("onramp.workflow_worker")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    provider = configure_tracing(settings, process_role="workflow-worker")
    engine = make_engine(str(settings.database_url))
    marketplace_engine = make_engine(str(settings.marketplace_database_url))
    sessions = make_session_factory(engine)
    marketplace_sessions = make_session_factory(marketplace_engine)
    marketplace = SqlMarketplaceReader(marketplace_sessions)
    writer = SqlMarketplaceWriter(marketplace_sessions)
    activities = WorkflowActivities(
        sessions,
        CheckService(sessions, marketplace, EvidenceRequester(writer)),
        ActionGateway(sessions, marketplace, writer),
    )
    log.info("workflow worker started queue=%s", settings.temporal_workflow_task_queue)
    try:
        asyncio.run(
            run_worker(
                address=settings.temporal_address,
                namespace=settings.temporal_namespace,
                task_queue=settings.temporal_workflow_task_queue,
                workflows=[CaseWorkflow],
                activities=[
                    activities.read_case,
                    activities.run_case_checks,
                    activities.resume_settlement_checks,
                    activities.execute_approved_action,
                    activities.recover_action_verification,
                ],
            )
        )
    finally:
        log.info("workflow worker stopped")
        engine.dispose()
        marketplace_engine.dispose()
        provider.shutdown()


if __name__ == "__main__":
    main()
