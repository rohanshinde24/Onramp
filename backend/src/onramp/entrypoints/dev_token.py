"""Mint a bearer token for a local user: ``onramp-dev-token <tenant_id> <user_id>``.

Refuses to run outside local and test environments.
"""

import sys
from datetime import UTC, datetime

from onramp.config import AppEnv, get_settings
from onramp.identity.tokens import issue_token


def main() -> None:
    settings = get_settings()
    if settings.app_env not in (AppEnv.LOCAL, AppEnv.TEST):
        sys.exit("onramp-dev-token only runs with APP_ENV=local or test")
    if len(sys.argv) != 3:
        sys.exit("usage: onramp-dev-token <tenant_id> <user_id>")
    tenant_id, user_id = sys.argv[1], sys.argv[2]
    print(
        issue_token(
            secret=settings.auth_jwt_secret.get_secret_value(),
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
            user_id=user_id,
            tenant_id=tenant_id,
            now=datetime.now(UTC),
        )
    )


if __name__ == "__main__":
    main()
