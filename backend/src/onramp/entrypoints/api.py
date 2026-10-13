"""Process entrypoint for the onramp-api role.

Uvicorn handles SIGTERM by refusing new connections and letting in-flight requests finish
within the graceful timeout.
"""

import uvicorn

from onramp.api.app import create_app
from onramp.config import get_settings
from onramp.telemetry.logging import configure_logging
from onramp.telemetry.tracing import configure_tracing


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    provider = configure_tracing(settings, process_role="api")
    try:
        uvicorn.run(
            create_app(),
            host="0.0.0.0",  # noqa: S104 - container entrypoint, bound inside the container network
            port=8000,
            log_config=None,
            timeout_graceful_shutdown=20,
        )
    finally:
        provider.shutdown()


if __name__ == "__main__":
    main()
