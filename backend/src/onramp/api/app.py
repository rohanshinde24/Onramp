from collections.abc import Sequence

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from onramp.api import health
from onramp.api.health import ReadinessCheck
from onramp.api.middleware import request_id_middleware


def create_app(readiness_checks: Sequence[ReadinessCheck] = ()) -> FastAPI:
    app = FastAPI(title="Onramp API", version="0.1.0")
    app.state.readiness_checks = list(readiness_checks)
    app.middleware("http")(request_id_middleware)
    app.include_router(health.router)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz")
    return app
