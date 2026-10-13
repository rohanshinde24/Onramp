"""OpenTelemetry tracer setup.

Without an OTLP endpoint, spans are still created (so trace IDs appear in logs and propagate
across boundaries) but are not exported anywhere.
"""

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from onramp.config import Settings


def configure_tracing(settings: Settings, process_role: str) -> TracerProvider:
    resource = Resource.create(
        {
            "service.name": settings.service_name,
            "service.namespace": "onramp",
            "onramp.process_role": process_role,
            "deployment.environment": settings.app_env.value,
        }
    )
    provider = TracerProvider(resource=resource)
    if settings.otel_exporter_otlp_endpoint:
        exporter = OTLPSpanExporter(
            endpoint=f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/traces"
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return provider
