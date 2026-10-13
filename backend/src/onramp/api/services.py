from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from onramp.approvals.service import ApprovalService
from onramp.cases.service import CaseService
from onramp.checks.service import CheckService
from onramp.proposals.service import ProposalService
from onramp.workflows.client import CaseCoordinator


@dataclass(frozen=True)
class AuthConfig:
    secret: str
    issuer: str
    audience: str


@dataclass(frozen=True)
class AppServices:
    """Everything request handlers need, built once per process."""

    sessions: sessionmaker[Session]
    auth: AuthConfig
    cases: CaseService
    proposals: ProposalService
    approvals: ApprovalService
    checks: CheckService
    workflows: CaseCoordinator | None = None
