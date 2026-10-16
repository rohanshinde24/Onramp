"""One trace follows an event from the code that emitted it, through the outbox publisher and
Kafka headers, into the consumer that handles it."""

from collections.abc import Iterator, Sequence

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.events.consumer import ConsumerRunner, RawMessage
from onramp.events.contracts import CaseEscalatedV1
from onramp.events.envelope import EventEnvelope
from onramp.events.models import OutboxEvent
from onramp.events.outbox import emit
from onramp.events.publisher import OutboundMessage, OutboxPublisher, PublishResult
from tests.integration.test_consumer_runner import NOW, FakeDeadLetters, FakeSource


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    yield exporter
    processor.shutdown()


class Sink:
    def __init__(self) -> None:
        self.delivered: list[OutboundMessage] = []

    def publish(self, messages: Sequence[OutboundMessage]) -> list[PublishResult]:
        self.delivered.extend(messages)
        return [PublishResult(m.event_id) for m in messages]


class TraceRecordingHandler:
    group = "test-tracer"
    topics = ("onramp.cases",)
    handles = {"case.escalated": frozenset({1})}  # noqa: RUF012

    def __init__(self) -> None:
        self.seen_trace_ids: list[int] = []

    def handle(self, session: Session, envelope: EventEnvelope) -> None:
        self.seen_trace_ids.append(trace.get_current_span().get_span_context().trace_id)


def _by_name(spans: InMemorySpanExporter, name: str) -> ReadableSpan:
    (span,) = [s for s in spans.get_finished_spans() if s.name == name]
    return span


def test_trace_runs_from_emit_through_publish_to_consume(
    onramp_sessions: sessionmaker[Session], spans: InMemorySpanExporter
) -> None:
    tracer = trace.get_tracer("test")
    with (
        tracer.start_as_current_span("operator request") as request,
        onramp_sessions() as session,
        session.begin(),
    ):
        emit(
            session,
            event_type="case.escalated",
            tenant_id="acme",
            aggregate_id="case_1",
            payload=CaseEscalatedV1(reason="operator_judgment", from_state="created"),
            occurred_at=NOW,
        )
    trace_id = request.get_span_context().trace_id

    with onramp_sessions() as session:
        stored = session.scalar(select(OutboxEvent.trace_context))
    assert stored is not None
    assert f"{trace_id:032x}" in stored["traceparent"]

    # The publisher runs later, outside the request, and still continues its trace.
    sink = Sink()
    OutboxPublisher(onramp_sessions, sink, clock=lambda: NOW.replace(year=2100)).run_once()
    (message,) = sink.delivered
    publish = _by_name(spans, "publish onramp.cases")
    assert publish.context.trace_id == trace_id
    assert publish.parent is not None
    assert publish.parent.span_id == request.get_span_context().span_id

    handler = TraceRecordingHandler()
    runner = ConsumerRunner(onramp_sessions, FakeSource(), FakeDeadLetters(), handler)
    runner.process(
        RawMessage(
            message.topic,
            0,
            7,
            message.key.encode(),
            message.value,
            {k: v.encode() for k, v in message.headers.items()},
        )
    )

    consume = _by_name(spans, "process onramp.cases")
    assert consume.context.trace_id == trace_id
    assert consume.parent is not None
    assert consume.parent.span_id == publish.context.span_id
    assert consume.attributes is not None
    assert consume.attributes["onramp.consumer.outcome"] == "applied"
    assert handler.seen_trace_ids == [trace_id]


def test_events_emitted_outside_any_span_store_no_context(
    onramp_sessions: sessionmaker[Session],
) -> None:
    with onramp_sessions() as session, session.begin():
        emit(
            session,
            event_type="case.escalated",
            tenant_id="acme",
            aggregate_id="case_1",
            payload=CaseEscalatedV1(reason="operator_judgment", from_state="created"),
            occurred_at=NOW,
        )
    with onramp_sessions() as session:
        assert session.scalar(select(OutboxEvent.trace_context)) is None
