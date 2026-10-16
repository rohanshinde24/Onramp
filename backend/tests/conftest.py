import os
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# Integration tests read database URLs from the environment. Locally these default to the
# compose PostgreSQL; CI sets them explicitly.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://onramp:onramp-local@localhost:5432/onramp"
)
os.environ.setdefault(
    "MARKETPLACE_DATABASE_URL",
    "postgresql+psycopg://onramp:onramp-local@localhost:5432/marketplace",
)

os.environ.setdefault("AUTH_JWT_SECRET", "test-only-signing-key-not-a-secret-0123456789")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        path = str(item.path)
        if "tests/integration/" in path:
            item.add_marker(pytest.mark.integration)
        if "tests/kafka/" in path:
            item.add_marker(pytest.mark.kafka)


@dataclass
class Captured:
    reader: InMemoryMetricReader
    spans: InMemorySpanExporter

    def total(self, name: str, **attributes: str) -> float:
        """Cumulative value of a counter's points whose attributes include ``attributes``."""
        data = self.reader.get_metrics_data()
        total = 0.0
        for resource in data.resource_metrics if data else []:
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    if metric.name != name:
                        continue
                    for point in metric.data.data_points:
                        point_attributes = dict(point.attributes or {})
                        if all(point_attributes.get(k) == v for k, v in attributes.items()):
                            total += getattr(point, "value", getattr(point, "count", 0))
        return total


_metric_reader = InMemoryMetricReader()


@pytest.fixture
def telemetry() -> Iterator[Captured]:
    """Process-wide in-memory metrics and spans. Global providers can be set once per process,
    so tests compare totals before and after rather than expecting a clean slate."""
    if not isinstance(metrics.get_meter_provider(), MeterProvider):
        metrics.set_meter_provider(MeterProvider(metric_readers=[_metric_reader]))
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    yield Captured(_metric_reader, exporter)
    processor.shutdown()
