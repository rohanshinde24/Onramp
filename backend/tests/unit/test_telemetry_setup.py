import logging

import pytest
from opentelemetry.instrumentation.logging.handler import LoggingHandler

from onramp.config import Settings
from onramp.telemetry.logging import ContextFilter
from onramp.telemetry.setup import configure_telemetry


def _settings(endpoint: str | None) -> Settings:
    return Settings(
        auth_jwt_secret="x" * 40,
        otel_exporter_otlp_endpoint=endpoint,
    )


@pytest.fixture(autouse=True)
def _restore_root_handlers() -> object:
    root = logging.getLogger()
    before = list(root.handlers)
    yield
    root.handlers[:] = before


def test_without_an_endpoint_nothing_is_exported() -> None:
    telemetry = configure_telemetry(_settings(None), process_role="api")

    assert telemetry.logger_provider is None
    assert not any(isinstance(h, LoggingHandler) for h in logging.getLogger().handlers)
    telemetry.shutdown()


def test_with_an_endpoint_logs_are_exported_with_correlation_fields() -> None:
    telemetry = configure_telemetry(_settings("http://127.0.0.1:4318"), process_role="api")

    handlers = [h for h in logging.getLogger().handlers if isinstance(h, LoggingHandler)]
    assert len(handlers) == 1
    assert any(isinstance(f, ContextFilter) for f in handlers[0].filters)
    resource = telemetry.tracer_provider.resource.attributes
    assert resource["onramp.process_role"] == "api"
    # Shut down without flushing to a collector that is not running.
    handlers[0].close()
