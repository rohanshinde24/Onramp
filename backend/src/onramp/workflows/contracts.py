from dataclasses import dataclass
from datetime import datetime

WORKFLOW_TASK_QUEUE = "onramp-workflows"
AGENT_TASK_QUEUE = "onramp-agent"


def case_workflow_id(case_id: str) -> str:
    return f"case-{case_id}"


@dataclass(frozen=True)
class CaseWorkflowInput:
    tenant_id: str
    case_id: str
    agent_task_queue: str = AGENT_TASK_QUEUE


@dataclass(frozen=True)
class CaseSignal:
    event_id: str


@dataclass(frozen=True)
class CaseSnapshot:
    state: str
    wait_until: datetime | None = None
    proposal_id: str | None = None


@dataclass(frozen=True)
class WorkflowResult:
    state: str
    applied_signal_ids: tuple[str, ...]
