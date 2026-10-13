"""Apply migrations: ``onramp-migrate`` upgrades both databases to head.

Runs as a one-off step before new application processes start, never from inside them.
"""

import logging

import onramp.models  # noqa: F401 - every table must be mapped before any session is used
from onramp.config import get_settings
from onramp.db import migrate
from onramp.telemetry.logging import configure_logging

log = logging.getLogger("onramp.migrate")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    targets = {
        "onramp": str(settings.database_url),
        "marketplace": str(settings.marketplace_database_url),
    }
    for chain, url in targets.items():
        log.info("upgrading %s database to head", chain)
        migrate.upgrade(chain, url)
    log.info("migrations complete")


if __name__ == "__main__":
    main()
