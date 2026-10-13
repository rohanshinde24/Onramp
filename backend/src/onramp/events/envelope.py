"""The envelope every Onramp event travels in (ADR-004).

An event states a fact that has already happened. The envelope carries identity
(``event_id``), ordering within its aggregate (``aggregate_version``), and lineage
(``correlation_id``, ``causation_id``). The payload is validated against the contract for
``(event_type, schema_version)``.
"""

from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class EventEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(min_length=1, max_length=64)
    event_type: str = Field(pattern=r"^[a-z_]+\.[a-z_]+$")
    schema_version: int = Field(ge=1)
    occurred_at: AwareDatetime
    tenant_id: str = Field(min_length=1, max_length=64)
    aggregate_type: str = Field(min_length=1, max_length=32)
    aggregate_id: str = Field(min_length=1, max_length=64)
    # Strictly increasing per aggregate. Consumers that care about order ignore anything not
    # newer than the last version they applied.
    aggregate_version: int = Field(ge=1)
    correlation_id: str = Field(min_length=1, max_length=64)
    causation_id: str | None = Field(default=None, max_length=64)
    payload: dict[str, Any]

    @property
    def partition_key(self) -> str:
        """Kafka key. Events of one aggregate share a partition and so keep their order."""
        return self.aggregate_id

    def to_json(self) -> bytes:
        return self.model_dump_json().encode()

    @classmethod
    def from_json(cls, raw: bytes) -> "EventEnvelope":
        return cls.model_validate_json(raw)


def is_after(envelope: EventEnvelope, last_applied_version: int | None) -> bool:
    return last_applied_version is None or envelope.aggregate_version > last_applied_version


__all__ = ["EventEnvelope", "is_after"]
