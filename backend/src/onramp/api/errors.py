"""Map domain exceptions to HTTP responses in one place."""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError

from onramp.api.cases import InvalidCursorError
from onramp.cases.queries import CaseNotFoundError
from onramp.cases.service import (
    IntakeKeyConflictError,
    StaleCaseError,
    SubjectNotFoundError,
    WorkerMismatchError,
)
from onramp.cases.state import IllegalTransitionError
from onramp.identity.principal import PermissionDeniedError

_STATUS: dict[type[Exception], tuple[int, str]] = {
    PermissionDeniedError: (status.HTTP_403_FORBIDDEN, "forbidden"),
    SubjectNotFoundError: (status.HTTP_404_NOT_FOUND, "subject_not_found"),
    WorkerMismatchError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "worker_mismatch"),
    IntakeKeyConflictError: (status.HTTP_409_CONFLICT, "intake_key_conflict"),
    IllegalTransitionError: (status.HTTP_409_CONFLICT, "illegal_transition"),
    CaseNotFoundError: (status.HTTP_404_NOT_FOUND, "case_not_found"),
    StaleCaseError: (status.HTTP_409_CONFLICT, "stale_case"),
    StaleDataError: (status.HTTP_409_CONFLICT, "stale_case"),
    InvalidCursorError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_cursor"),
}


def install_error_handlers(app: FastAPI) -> None:
    for exc_type, (code, error) in _STATUS.items():
        app.add_exception_handler(exc_type, _handler(code, error))


def _handler(code: int, error: str) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    async def handle(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=code, content={"error": error, "detail": str(exc)})

    return handle
