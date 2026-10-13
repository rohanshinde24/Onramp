"""Best-effort direct workflow notifications; durable events provide the fallback."""

import logging

from onramp.workflows.client import CaseCoordinator

log = logging.getLogger(__name__)


def start_case(coordinator: CaseCoordinator | None, tenant_id: str, case_id: str) -> None:
    if coordinator is None:
        return
    try:
        coordinator.start_case(tenant_id, case_id)
    except Exception:
        log.exception("direct workflow start failed; durable fallback remains case_id=%s", case_id)


def signal_case(
    coordinator: CaseCoordinator | None,
    tenant_id: str,
    case_id: str,
    signal: str,
    event_id: str,
) -> None:
    if coordinator is None:
        return
    try:
        coordinator.signal_case(tenant_id, case_id, signal, event_id)
    except Exception:
        log.exception("direct workflow signal failed; durable fallback remains case_id=%s", case_id)
