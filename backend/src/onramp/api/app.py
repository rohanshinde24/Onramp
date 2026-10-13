from collections.abc import Sequence

from fastapi import FastAPI

from onramp.api import health
from onramp.api.health import ReadinessCheck


def create_app(readiness_checks: Sequence[ReadinessCheck] = ()) -> FastAPI:
    app = FastAPI(title="Onramp API", version="0.1.0")
    app.state.readiness_checks = list(readiness_checks)
    app.include_router(health.router)
    return app
