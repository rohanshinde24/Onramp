from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from onramp.marketplace import models as m
from onramp.marketplace.builder import WorldBuilder
from onramp.marketplace.faults import Fault, FaultPlan, FaultyMarketplaceWriter, OutcomeUnknownError
from onramp.marketplace.writer import SqlMarketplaceWriter

T0 = datetime(2026, 8, 20, 9, tzinfo=UTC)


@pytest.fixture
def sessions(marketplace_sessions: sessionmaker[Session]) -> sessionmaker[Session]:
    with marketplace_sessions() as session, session.begin():
        WorldBuilder(session, "acme").tenant().worker("w_1", "Sam Lee")
    return marketplace_sessions


def _payments(sessions: sessionmaker[Session]) -> int:
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(m.Payment)) or 0


def test_lost_response_then_retry_with_same_key_pays_once(
    sessions: sessionmaker[Session],
) -> None:
    plan = FaultPlan().inject("issue_adjustment", 1, Fault.LOST_RESPONSE)
    writer = FaultyMarketplaceWriter(SqlMarketplaceWriter(sessions), plan)

    with pytest.raises(OutcomeUnknownError):
        writer.issue_adjustment("acme", "w_1", 8400, "USD", idempotency_key="k")
    assert _payments(sessions) == 1  # the write landed even though the caller saw a failure

    retried = writer.issue_adjustment("acme", "w_1", 8400, "USD", idempotency_key="k")

    assert retried.amount_cents == 8400
    assert _payments(sessions) == 1


def test_timeout_before_write_changes_nothing(sessions: sessionmaker[Session]) -> None:
    plan = FaultPlan().inject("issue_adjustment", 1, Fault.TIMEOUT_BEFORE_WRITE)
    writer = FaultyMarketplaceWriter(SqlMarketplaceWriter(sessions), plan)

    with pytest.raises(OutcomeUnknownError):
        writer.issue_adjustment("acme", "w_1", 8400, "USD", idempotency_key="k")

    assert _payments(sessions) == 0
