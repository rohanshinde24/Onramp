"""The application services a case evaluation drives, and the clock they share."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from onramp.actions.gateway import ActionGateway
from onramp.agent.model import ChatModel
from onramp.approvals.service import ApprovalService
from onramp.cases.service import CaseService
from onramp.proposals.service import ProposalService
from onramp.workflows.activities import AgentActivities, WorkflowActivities

# Every step happens one second after the previous one, so records written by successive
# steps never share a timestamp.
STEP = timedelta(seconds=1)


class VirtualClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def tick(self) -> None:
        self._now += STEP

    def advance_to(self, deadline: datetime) -> None:
        self._now = max(self._now, deadline)


@dataclass(frozen=True)
class CaseServices:
    cases: CaseService
    workflow: WorkflowActivities
    agent: AgentActivities
    proposals: ProposalService
    approvals: ApprovalService
    # The same gateway the workflow activities use, for replaying an execution directly.
    gateway: ActionGateway


class ServiceFactory(Protocol):
    def __call__(self, clock: VirtualClock, model: ChatModel) -> CaseServices: ...
