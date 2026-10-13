"""Process entrypoint for the onramp-api role.

Uvicorn handles SIGTERM by refusing new connections and letting in-flight requests finish
within the graceful timeout.
"""

import uvicorn

from onramp.api.app import create_app
from onramp.api.services import AppServices, AuthConfig
from onramp.cases.service import CaseService
from onramp.config import get_settings
from onramp.db.engine import make_engine, make_session_factory
from onramp.db.readiness import database_check
from onramp.marketplace.reader import SqlMarketplaceReader
from onramp.telemetry.logging import configure_logging
from onramp.telemetry.tracing import configure_tracing


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    provider = configure_tracing(settings, process_role="api")
    engine = make_engine(str(settings.database_url))
    marketplace_engine = make_engine(str(settings.marketplace_database_url))
    sessions = make_session_factory(engine)
    services = AppServices(
        sessions=sessions,
        auth=AuthConfig(
            secret=settings.auth_jwt_secret.get_secret_value(),
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
        ),
        cases=CaseService(sessions, SqlMarketplaceReader(make_session_factory(marketplace_engine))),
    )
    app = create_app(
        services,
        readiness_checks=[
            database_check("onramp_db", engine),
            database_check("marketplace_db", marketplace_engine),
        ],
    )
    try:
        uvicorn.run(
            app,
            host="0.0.0.0",  # noqa: S104 - container entrypoint, bound inside the container network
            port=8000,
            log_config=None,
            timeout_graceful_shutdown=20,
        )
    finally:
        engine.dispose()
        marketplace_engine.dispose()
        provider.shutdown()


if __name__ == "__main__":
    main()
