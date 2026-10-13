"""Import point for every onramp-database model, used by Alembic autogenerate."""

from onramp.identity.models import Tenant, User

__all__ = ["Tenant", "User"]
