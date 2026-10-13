from pathlib import Path

from alembic import command
from alembic.config import Config

ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"


def alembic_config(chain: str, database_url: str) -> Config:
    config = Config(str(ALEMBIC_INI), ini_section=chain)
    config.attributes["database_url"] = database_url
    return config


def upgrade(chain: str, database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(chain, database_url), revision)


def downgrade(chain: str, database_url: str, revision: str = "base") -> None:
    command.downgrade(alembic_config(chain, database_url), revision)
