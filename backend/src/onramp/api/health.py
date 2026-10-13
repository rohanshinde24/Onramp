"""Liveness and readiness endpoints.

Liveness answers "is the process able to serve at all" and never touches dependencies, so a
database outage does not get the process restarted. Readiness runs the registered dependency
checks and reports which one failed.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    probe: Callable[[], Awaitable[None]]


class CheckResult(BaseModel):
    name: str
    ok: bool
    error: str | None = None


class ReadinessReport(BaseModel):
    ready: bool
    checks: list[CheckResult]


router = APIRouter(tags=["health"])


@router.get("/healthz")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", response_model=ReadinessReport)
async def readiness(request: Request, response: Response) -> ReadinessReport:
    checks: Sequence[ReadinessCheck] = request.app.state.readiness_checks
    results = [await _run(check) for check in checks]
    ready = all(result.ok for result in results)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessReport(ready=ready, checks=results)


async def _run(check: ReadinessCheck) -> CheckResult:
    try:
        await check.probe()
    except Exception as exc:  # a failing probe must report, not crash the endpoint
        return CheckResult(name=check.name, ok=False, error=type(exc).__name__)
    return CheckResult(name=check.name, ok=True)
