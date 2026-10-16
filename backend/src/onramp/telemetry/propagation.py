"""Trace context that crosses the transactional outbox and Kafka.

An event is written in one process and published and consumed in others, possibly much later.
The W3C trace context of the code that emitted it is stored with the outbox row, carried in
Kafka headers, and restored by the consumer, so one trace follows a fact from the request that
caused it to every effect it has.
"""

from collections.abc import Mapping

from opentelemetry import context, propagate


def current_carrier() -> dict[str, str] | None:
    """The active trace context as headers, or None outside any span."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier or None


def context_from(carrier: Mapping[str, str] | None) -> context.Context:
    return propagate.extract(dict(carrier or {}))


def decode_headers(headers: Mapping[str, bytes]) -> dict[str, str]:
    """Kafka header values are bytes; anything that is not text is not trace context."""
    decoded = {}
    for name, value in headers.items():
        try:
            decoded[name] = value.decode()
        except UnicodeDecodeError:
            continue
    return decoded
