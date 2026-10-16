"""Business counters count committed facts, not attempts."""

import pytest
from opentelemetry.metrics import Counter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, NumberDataPoint
from sqlalchemy.orm import Session, sessionmaker

from onramp.telemetry.metrics import record_on_commit


class RollbackError(Exception):
    pass


@pytest.fixture
def counted() -> tuple[InMemoryMetricReader, Counter]:
    reader = InMemoryMetricReader()
    counter = MeterProvider(metric_readers=[reader]).get_meter("test").create_counter("things")
    return reader, counter


def _total(reader: InMemoryMetricReader) -> int:
    data = reader.get_metrics_data()
    total = 0
    for resource in data.resource_metrics if data else []:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                for point in metric.data.data_points:
                    assert isinstance(point, NumberDataPoint)
                    total += int(point.value)
    return total


def _stage_then_fail(sessions: sessionmaker[Session], counter: Counter) -> None:
    with sessions() as session, session.begin():
        record_on_commit(session, counter, 5, {})
        raise RollbackError


def test_recorded_only_when_the_transaction_commits(
    onramp_sessions: sessionmaker[Session], counted: tuple[InMemoryMetricReader, Counter]
) -> None:
    reader, counter = counted
    with onramp_sessions() as session, session.begin():
        record_on_commit(session, counter, 1, {"k": "v"})
        assert _total(reader) == 0
    assert _total(reader) == 1

    with pytest.raises(RollbackError):
        _stage_then_fail(onramp_sessions, counter)
    assert _total(reader) == 1


def test_a_rolled_back_savepoint_drops_only_its_own_metrics(
    onramp_sessions: sessionmaker[Session], counted: tuple[InMemoryMetricReader, Counter]
) -> None:
    reader, counter = counted

    def failing_savepoint(session: Session) -> None:
        with session.begin_nested():
            record_on_commit(session, counter, 10, {})
            raise RollbackError

    with onramp_sessions() as session, session.begin():
        record_on_commit(session, counter, 1, {})
        with pytest.raises(RollbackError):
            failing_savepoint(session)
        record_on_commit(session, counter, 100, {})

    assert _total(reader) == 101
