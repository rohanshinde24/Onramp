from fastapi import APIRouter
from pydantic import BaseModel

from onramp.api.auth import CurrentPrincipal
from onramp.identity.roles import ROLE_PERMISSIONS

router = APIRouter(tags=["identity"])


class Me(BaseModel):
    user_id: str
    tenant_id: str
    role: str
    permissions: list[str]


@router.get("/me", response_model=Me)
def me(principal: CurrentPrincipal) -> Me:
    return Me(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        role=principal.role.value,
        permissions=sorted(p.value for p in ROLE_PERMISSIONS[principal.role]),
    )
