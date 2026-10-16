import json
import logging

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider

from onramp.api.app import create_app
from onramp.telemetry.logging import ContextFilter, JsonFormatter, current_log_context, log_context


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord("onramp.test", logging.INFO, __file__, 1, message, None, None)


def test_log_line_carries_bound_context() -> None:
    with log_context(case_id="case-1", tenant_id="t-1"):
        line = json.loads(JsonFormatter().format(_record("checked")))

    assert line["message"] == "checked"
    assert line["case_id"] == "case-1"
    assert line["tenant_id"] == "t-1"


def test_context_is_restored_after_block() -> None:
    with log_context(case_id="outer"):
        with log_context(case_id="inner", event_id="e-1"):
            assert current_log_context() == {"case_id": "inner", "event_id": "e-1"}
        assert current_log_context() == {"case_id": "outer"}
    assert current_log_context() == {}


def test_unknown_context_field_is_rejected() -> None:
    with pytest.raises(ValueError, match="worker_ssn"), log_context(worker_ssn="x"):
        pass


def test_log_line_includes_active_trace_ids() -> None:
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("work") as span:
        line = json.loads(JsonFormatter().format(_record("inside span")))

    assert line["trace_id"] == format(span.get_span_context().trace_id, "032x")


def test_request_id_is_generated_and_echoed() -> None:
    response = TestClient(create_app()).get("/healthz")

    assert len(response.headers["X-Request-ID"]) == 32


def test_plain_caller_request_id_is_kept() -> None:
    response = TestClient(create_app()).get("/healthz", headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"


def test_unsafe_caller_request_id_is_replaced() -> None:
    response = TestClient(create_app()).get("/healthz", headers={"X-Request-ID": 'x" injected="1'})

    assert response.headers["X-Request-ID"] != 'x" injected="1'


def test_error_responses_hide_library_internals() -> None:
    from fastapi import FastAPI
    from sqlalchemy.orm.exc import StaleDataError

    from onramp.api.errors import install_error_handlers

    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise StaleDataError("UPDATE statement on table 'support_case' matched 0 rows")

    response = TestClient(app).get("/boom")

    assert response.status_code == 409
    assert response.json() == {"error": "stale_case", "detail": None}


def test_context_filter_copies_bound_fields_onto_records() -> None:
    record = _record("exported")
    with log_context(case_id="case-9", correlation_id="corr-1"):
        assert ContextFilter().filter(record)

    assert (record.case_id, record.correlation_id) == ("case-9", "corr-1")  # type: ignore[attr-defined]
