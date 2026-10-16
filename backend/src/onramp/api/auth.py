from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from onramp.api.services import AppServices
from onramp.identity.models import User
from onramp.identity.principal import Principal
from onramp.identity.roles import Role
from onramp.identity.tokens import InvalidTokenError, verify_token

_bearer = HTTPBearer(auto_error=False)

# One response for every authentication failure, so callers cannot probe which part failed.
_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_services(request: Request) -> AppServices:
    services: AppServices = request.app.state.services
    return services


def get_principal(
    services: Annotated[AppServices, Depends(get_services)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise _UNAUTHENTICATED
    try:
        claims = verify_token(
            credentials.credentials,
            secret=services.auth.secret,
            issuer=services.auth.issuer,
            audience=services.auth.audience,
        )
    except InvalidTokenError:
        raise _UNAUTHENTICATED from None

    with services.sessions() as session:
        user = session.scalar(select(User).filter_by(tenant_id=claims.tenant_id, id=claims.user_id))
    if user is None:
        raise _UNAUTHENTICATED
    return Principal(tenant_id=user.tenant_id, user_id=user.id, role=Role(user.role))


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
Services = Annotated[AppServices, Depends(get_services)]
