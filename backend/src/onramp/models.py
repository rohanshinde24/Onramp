"""Import point for every onramp-database model, used by Alembic autogenerate."""

from onramp.approvals.models import Approval
from onramp.audit.models import AuditEvent
from onramp.cases.models import SupportCase
from onramp.events.models import AggregateSequence, OutboxEvent
from onramp.identity.models import Tenant, User
from onramp.proposals.models import Proposal

__all__ = [
    "AggregateSequence",
    "Approval",
    "AuditEvent",
    "OutboxEvent",
    "Proposal",
    "SupportCase",
    "Tenant",
    "User",
]
