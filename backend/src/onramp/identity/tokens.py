"""Bearer tokens for the API.

A token proves who the caller is (user and tenant). It deliberately carries no role: the role
is read from the database on every request, so a token cannot grant itself permissions and a
role change takes effect immediately.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt

ALGORITHM = "HS256"


class InvalidTokenError(Exception):
    pass


@dataclass(frozen=True)
class TokenClaims:
    user_id: str
    tenant_id: str


def issue_token(
    *,
    secret: str,
    issuer: str,
    audience: str,
    user_id: str,
    tenant_id: str,
    now: datetime,
    ttl: timedelta = timedelta(hours=8),
) -> str:
    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": user_id,
        "tid": tenant_id,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def verify_token(token: str, *, secret: str, issuer: str, audience: str) -> TokenClaims:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            issuer=issuer,
            audience=audience,
            options={"require": ["iss", "aud", "sub", "tid", "iat", "exp"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(type(exc).__name__) from exc
    user_id, tenant_id = payload["sub"], payload["tid"]
    if not isinstance(user_id, str) or not isinstance(tenant_id, str):
        raise InvalidTokenError("malformed subject")
    return TokenClaims(user_id=user_id, tenant_id=tenant_id)
