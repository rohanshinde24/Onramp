"""Map domain exceptions to HTTP responses in one place."""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError

from onramp.api.cases import InvalidCursorError
from onramp.api.policies import PassageNotFoundError
from onramp.approvals.service import (
    AlreadyDecidedError,
    ContentMismatchError,
    NotAwaitingApprovalError,
    SelfApprovalError,
)
from onramp.cases.queries import CaseNotFoundError
from onramp.cases.service import (
    IntakeKeyConflictError,
    StaleCaseError,
    SubjectNotFoundError,
    WorkerMismatchError,
)
from onramp.cases.state import IllegalTransitionError
from onramp.identity.principal import PermissionDeniedError
from onramp.policy.applicability import NoApplicablePolicyError
from onramp.proposals.service import (
    InvalidProposalError,
    ProposalNotFoundError,
    ProposalNotPendingError,
)

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
    PassageNotFoundError: (status.HTTP_404_NOT_FOUND, "passage_not_found"),
    NoApplicablePolicyError: (status.HTTP_409_CONFLICT, "no_applicable_policy"),
    InvalidProposalError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_proposal"),
    ProposalNotFoundError: (status.HTTP_404_NOT_FOUND, "proposal_not_found"),
    ProposalNotPendingError: (status.HTTP_409_CONFLICT, "proposal_not_pending"),
    NotAwaitingApprovalError: (status.HTTP_409_CONFLICT, "not_awaiting_approval"),
    AlreadyDecidedError: (status.HTTP_409_CONFLICT, "already_decided"),
    ContentMismatchError: (status.HTTP_409_CONFLICT, "content_mismatch"),
    SelfApprovalError: (status.HTTP_403_FORBIDDEN, "self_approval"),
}


# Library exceptions whose messages describe internals; clients get only the error code.
_NO_DETAIL: frozenset[type[Exception]] = frozenset({StaleDataError})


def install_error_handlers(app: FastAPI) -> None:
    for exc_type, (code, error) in _STATUS.items():
        app.add_exception_handler(exc_type, _handler(code, error, exc_type not in _NO_DETAIL))


def _handler(
    code: int, error: str, with_detail: bool
) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    async def handle(_: Request, exc: Exception) -> JSONResponse:
        detail = str(exc) if with_detail else None
        return JSONResponse(status_code=code, content={"error": error, "detail": detail})

    return handle
