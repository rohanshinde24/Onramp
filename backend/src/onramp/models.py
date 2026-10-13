"""Import point for every onramp-database model, used by Alembic autogenerate."""

from onramp.audit.models import AuditEvent
from onramp.cases.models import SupportCase
from onramp.identity.models import Tenant, User
from onramp.proposals.models import Proposal

__all__ = ["AuditEvent", "Proposal", "SupportCase", "Tenant", "User"]
