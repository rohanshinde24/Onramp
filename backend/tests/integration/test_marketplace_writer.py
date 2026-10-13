from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from onramp.marketplace import models as m
from onramp.marketplace.builder import WorldBuilder
from onramp.marketplace.writer import (
    IdempotencyConflictError,
    NotFoundError,
    RejectedError,
    SqlMarketplaceWriter,
)

T0 = datetime(2026, 8, 20, 9, tzinfo=UTC)


def _seed(sessions: sessionmaker[Session]) -> None:
    with sessions() as session, session.begin():
        acme = WorldBuilder(session, "acme").tenant()
        acme.worker("w_1", "Sam Lee")
        acme.worker("w_denied", "Ana Diaz", status="denied")
        acme.task("t_1", 8400)
        acme.assignment("a_1", "t_1", "w_1", T0)
        acme.submission("s_1", "a_1", T0)
        acme.review("qr_fail", "s_1", "fail", "0.40", T0)
        acme.review("qr_pass", "s_1", "pass", "0.95", T0)
        WorldBuilder(session, "globex").tenant().worker("w_g", "Sam Lee")


def _count(sessions: sessionmaker[Session], model: type[m.MarketplaceBase]) -> int:
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


@pytest.fixture
def writer(marketplace_sessions: sessionmaker[Session]) -> SqlMarketplaceWriter:
    _seed(marketplace_sessions)
    return SqlMarketplaceWriter(marketplace_sessions, clock=lambda: T0)


def test_repeated_key_returns_original_payment(
    writer: SqlMarketplaceWriter, marketplace_sessions: sessionmaker[Session]
) -> None:
    first = writer.issue_adjustment("acme", "w_1", 8400, "USD", idempotency_key="k1")
    second = writer.issue_adjustment("acme", "w_1", 8400, "USD", idempotency_key="k1")

    assert second == first
    assert _count(marketplace_sessions, m.Payment) == 1
    assert _count(marketplace_sessions, m.PaymentEvent) == 2


def test_reused_key_with_different_request_is_rejected(
    writer: SqlMarketplaceWriter, marketplace_sessions: sessionmaker[Session]
) -> None:
    writer.issue_adjustment("acme", "w_1", 8400, "USD", idempotency_key="k1")

    with pytest.raises(IdempotencyConflictError):
        writer.issue_adjustment("acme", "w_1", 50_000, "USD", idempotency_key="k1")
    assert _count(marketplace_sessions, m.Payment) == 1


def test_rejected_write_leaves_key_unused(
    writer: SqlMarketplaceWriter, marketplace_sessions: sessionmaker[Session]
) -> None:
    with pytest.raises(RejectedError):
        writer.activate_worker("acme", "w_denied", idempotency_key="k2")

    assert _count(marketplace_sessions, m.WriteRequest) == 0


def test_cannot_write_to_another_tenants_worker(writer: SqlMarketplaceWriter) -> None:
    with pytest.raises(NotFoundError):
        writer.issue_adjustment("acme", "w_g", 100, "USD", idempotency_key="k3")


def test_only_failed_reviews_can_be_overturned(writer: SqlMarketplaceWriter) -> None:
    overturned = writer.overturn_review("acme", "qr_fail", idempotency_key="k4")

    assert overturned.result == "pass"
    assert overturned.overturned_at == T0
    with pytest.raises(RejectedError):
        writer.overturn_review("acme", "qr_pass", idempotency_key="k5")


def test_concurrent_calls_with_one_key_write_once(
    marketplace_committed: sessionmaker[Session],
) -> None:
    _seed(marketplace_committed)
    writer = SqlMarketplaceWriter(marketplace_committed)

    def call(_: int) -> str:
        return writer.issue_adjustment("acme", "w_1", 8400, "USD", idempotency_key="race").id

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = set(pool.map(call, range(16)))

    assert len(ids) == 1
    assert _count(marketplace_committed, m.Payment) == 1
