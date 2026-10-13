import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column


def new_id(prefix: str) -> str:
    """Opaque string ID with a type prefix, e.g. ``case_3f2a...``.

    Prefixes make IDs self-describing in logs, evidence references and audit history.
    """
    return f"{prefix}_{uuid.uuid4().hex}"


ID_LENGTH = 64


def id_column(*, primary_key: bool = False) -> Mapped[str]:
    return mapped_column(String(ID_LENGTH), primary_key=primary_key)


def created_at_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
