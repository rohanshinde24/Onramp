"""OpenTelemetry setup for one process role: traces, metrics and logs.

Without an OTLP endpoint, spans and instruments still exist (so trace IDs appear in logs and
propagate across boundaries, and code records metrics unconditionally) but nothing is exported.
With one, all three signals go to the same collector, tagged with the process role.
"""

import logging
from dataclasses import dataclass

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sqlalchemy import Engine

from onramp.config import Settings
from onramp.telemetry.logging import ContextFilter

METRIC_EXPORT_INTERVAL_MS = 10_000


@dataclass
class Telemetry:
    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    logger_provider: LoggerProvider | None

    def shutdown(self) -> None:
        """Flush what is buffered. Each role calls this last, after its work has stopped."""
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()
        if self.logger_provider is not None:
            self.logger_provider.shutdown()


def configure_telemetry(settings: Settings, process_role: str) -> Telemetry:
    resource = Resource.create(
        {
            # One service per role, so dashboards and trace views tell the API from the workers.
            "service.name": f"{settings.service_name}-{process_role}",
            "service.namespace": "onramp",
            "onramp.process_role": process_role,
            "deployment.environment": settings.app_env.value,
        }
    )
    endpoint = (settings.otel_exporter_otlp_endpoint or "").rstrip("/")

    tracer_provider = TracerProvider(resource=resource)
    if endpoint:
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
        )
    trace.set_tracer_provider(tracer_provider)

    readers = (
        [
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics"),
                export_interval_millis=METRIC_EXPORT_INTERVAL_MS,
            )
        ]
        if endpoint
        else []
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=readers)
    metrics.set_meter_provider(meter_provider)

    logger_provider = None
    if endpoint:
        # Logs still go to stdout as JSON; this sends the same records, with their correlation
        # fields and trace IDs, to the collector so a trace links to its logs.
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{endpoint}/v1/logs"))
        )
        set_logger_provider(logger_provider)
        handler = LoggingHandler(logger_provider=logger_provider)
        handler.addFilter(ContextFilter())
        logging.getLogger().addHandler(handler)

    return Telemetry(tracer_provider, meter_provider, logger_provider)


def instrument_databases(*engines: Engine) -> None:
    """One span per SQL statement, so a slow or failing query shows inside its request trace."""
    SQLAlchemyInstrumentor().instrument(engines=list(engines), enable_commenter=False)
