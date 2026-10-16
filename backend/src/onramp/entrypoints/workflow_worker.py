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
from onramp.telemetry.setup import configure_telemetry, instrument_databases
from onramp.workflows.activities import WorkflowActivities
from onramp.workflows.case import CaseWorkflow
from onramp.workflows.client import TemporalCaseCoordinator
from onramp.workflows.reconcile import reconcile_case_workflows

log = logging.getLogger("onramp.workflow_worker")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    telemetry = configure_telemetry(settings, process_role="workflow-worker")
    engine = make_engine(str(settings.database_url))
    marketplace_engine = make_engine(str(settings.marketplace_database_url))
    instrument_databases(engine, marketplace_engine)
    sessions = make_session_factory(engine)
    marketplace_sessions = make_session_factory(marketplace_engine)
    marketplace = SqlMarketplaceReader(marketplace_sessions)
    writer = SqlMarketplaceWriter(marketplace_sessions)
    activities = WorkflowActivities(
        sessions,
        CheckService(sessions, marketplace, EvidenceRequester(writer)),
        ActionGateway(sessions, marketplace, writer),
    )
    coordinator = TemporalCaseCoordinator(
        settings.temporal_address,
        settings.temporal_namespace,
        settings.temporal_workflow_task_queue,
        settings.temporal_agent_task_queue,
    )
    recovered = reconcile_case_workflows(sessions, coordinator)
    coordinator.close()
    log.info("workflow reconciliation complete open_cases=%d", recovered)
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
                    activities.expire_evidence_wait,
                    activities.execute_approved_action,
                    activities.recover_action_verification,
                ],
            )
        )
    finally:
        log.info("workflow worker stopped")
        engine.dispose()
        marketplace_engine.dispose()
        telemetry.shutdown()


if __name__ == "__main__":
    main()
