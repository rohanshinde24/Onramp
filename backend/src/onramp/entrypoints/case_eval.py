"""Wiring for whole-application case evaluation.

The evaluation package may not import the marketplace writer (ADR-008), so the services that
hold one are assembled here.
"""

from sqlalchemy.orm import Session, sessionmaker

from onramp.actions.evidence import EvidenceRequester
from onramp.actions.gateway import ActionGateway
from onramp.agent.model import ChatModel
from onramp.approvals.service import ApprovalService
from onramp.cases.service import CaseService
from onramp.checks.service import CheckService
from onramp.evaluation.runner import CaseServices, VirtualClock
from onramp.investigations.service import InvestigationService
from onramp.marketplace.reader import SqlMarketplaceReader
from onramp.marketplace.writer import MarketplaceWriter, SqlMarketplaceWriter
from onramp.policy.search import PolicyRetriever
from onramp.proposals.service import ProposalService
from onramp.workflows.activities import AgentActivities, WorkflowActivities


def build_services(
    onramp: sessionmaker[Session],
    marketplace: sessionmaker[Session],
    retriever: PolicyRetriever,
    clock: VirtualClock,
    model: ChatModel,
    writer: MarketplaceWriter | None = None,
) -> CaseServices:
    reader = SqlMarketplaceReader(marketplace)
    writer = writer or SqlMarketplaceWriter(marketplace, clock)
    checks = CheckService(onramp, reader, EvidenceRequester(writer), clock)
    investigations = InvestigationService(onramp, reader, retriever, model, clock=clock)
    return CaseServices(
        cases=CaseService(onramp, reader, clock),
        workflow=WorkflowActivities(
            onramp, checks, ActionGateway(onramp, reader, writer, clock), clock
        ),
        agent=AgentActivities(onramp, investigations),
        proposals=ProposalService(onramp, clock),
        approvals=ApprovalService(onramp, clock),
    )
