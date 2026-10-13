"""Process entrypoint for the onramp-api role.

Uvicorn handles SIGTERM by refusing new connections and letting in-flight requests finish
within the graceful timeout.
"""

import uvicorn

from onramp.api.app import create_app
from onramp.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(),
        host="0.0.0.0",  # noqa: S104 - container entrypoint, bound inside the container network
        port=8000,
        log_level=settings.log_level.lower(),
        timeout_graceful_shutdown=20,
    )


if __name__ == "__main__":
    main()
