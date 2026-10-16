from fastapi import APIRouter
from pydantic import BaseModel

from onramp.api.auth import CurrentPrincipal, Services
from onramp.identity.models import Tenant, User
from onramp.identity.roles import ROLE_PERMISSIONS

router = APIRouter(tags=["identity"])


class Me(BaseModel):
    user_id: str
    tenant_id: str
    role: str
    permissions: list[str]
    display_name: str
    tenant_name: str
    # The console shows a lead whether an adjustment is within what they may approve; the
    # gateway and approval service enforce it regardless.
    payout_approval_limit_cents: int


@router.get("/me", response_model=Me)
def me(principal: CurrentPrincipal, services: Services) -> Me:
    with services.sessions() as session:
        user = session.get_one(User, principal.user_id)
        tenant = session.get_one(Tenant, principal.tenant_id)
        return Me(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            role=principal.role.value,
            permissions=sorted(p.value for p in ROLE_PERMISSIONS[principal.role]),
            display_name=user.display_name,
            tenant_name=tenant.name,
            payout_approval_limit_cents=tenant.payout_approval_limit_cents,
        )
