"""Import point for every onramp-database model, used by Alembic autogenerate."""

from onramp.audit.models import AuditEvent
from onramp.cases.models import SupportCase
from onramp.identity.models import Tenant, User

__all__ = ["AuditEvent", "SupportCase", "Tenant", "User"]
