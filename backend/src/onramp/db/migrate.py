import os
from pathlib import Path

from alembic import command
from alembic.config import Config

CHAINS = ("onramp", "marketplace")


def alembic_ini() -> Path:
    """alembic.ini from ONRAMP_ALEMBIC_INI (set in the container image), else the source tree."""
    override = os.environ.get("ONRAMP_ALEMBIC_INI")
    return Path(override) if override else Path(__file__).resolve().parents[3] / "alembic.ini"


def alembic_config(chain: str, database_url: str) -> Config:
    if chain not in CHAINS:
        raise ValueError(f"unknown migration chain {chain!r}")
    config = Config(str(alembic_ini()), ini_section=chain)
    config.attributes["database_url"] = database_url
    return config


def upgrade(chain: str, database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(chain, database_url), revision)


def downgrade(chain: str, database_url: str, revision: str = "base") -> None:
    command.downgrade(alembic_config(chain, database_url), revision)
