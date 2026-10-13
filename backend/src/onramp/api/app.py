from collections.abc import Sequence

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from onramp.api import cases, health, me
from onramp.api.errors import install_error_handlers
from onramp.api.health import ReadinessCheck
from onramp.api.middleware import request_id_middleware
from onramp.api.services import AppServices


def create_app(
    services: AppServices | None = None, readiness_checks: Sequence[ReadinessCheck] = ()
) -> FastAPI:
    """Build the API. Without services, only health endpoints are mounted."""
    app = FastAPI(title="Onramp API", version="0.1.0")
    app.state.readiness_checks = list(readiness_checks)
    app.middleware("http")(request_id_middleware)
    app.include_router(health.router)
    if services is not None:
        app.state.services = services
        install_error_handlers(app)
        app.include_router(me.router)
        app.include_router(cases.router)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz")
    return app
