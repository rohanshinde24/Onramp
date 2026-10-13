from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Explicit constraint names keep migrations deterministic and errors readable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class OnrampBase(DeclarativeBase):
    """Declarative base for tables in the onramp database."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
