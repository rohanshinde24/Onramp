"""Structured JSON logging with correlation context.

Context such as request_id, tenant_id and case_id is bound once per unit of work and appears
on every log line written inside it, including lines from libraries.
"""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

CONTEXT_FIELDS = (
    "request_id",
    "tenant_id",
    "case_id",
    "workflow_id",
    "event_id",
    "correlation_id",
    "causation_id",
)

_context: ContextVar[dict[str, str]] = ContextVar("onramp_log_context", default={})  # noqa: B039


@contextmanager
def log_context(**fields: str) -> Iterator[None]:
    unknown = set(fields) - set(CONTEXT_FIELDS)
    if unknown:
        raise ValueError(f"unknown log context fields: {sorted(unknown)}")
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def current_log_context() -> dict[str, str]:
    return dict(_context.get())


class ContextFilter(logging.Filter):
    """Copies the bound correlation fields onto each record, for handlers that export record
    attributes (the OTLP log handler) rather than formatting through JsonFormatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in _context.get().items():
            setattr(record, key, value)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_context.get(),
        }
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            entry["trace_id"] = format(span_context.trace_id, "032x")
            entry["span_id"] = format(span_context.span_id, "016x")
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
