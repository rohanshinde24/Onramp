import re
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from onramp.telemetry.logging import log_context

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Bind a request ID to the log context and echo it in the response.

    A caller-supplied ID is accepted only if it is short and plain, so it cannot inject content
    into logs.
    """
    supplied = request.headers.get(REQUEST_ID_HEADER, "")
    request_id = supplied if _VALID_REQUEST_ID.match(supplied) else uuid.uuid4().hex
    with log_context(request_id=request_id):
        response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response
