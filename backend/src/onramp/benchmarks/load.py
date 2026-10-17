"""Load benchmark of the intake pipeline.

A run seeds fresh marketplace subjects in the ``loadtest`` tenant, k6 opens one case per subject
through the API at a fixed arrival rate, and the real pipeline carries each case on: the API
commits it with a ``case.created`` outbox row and starts its workflow, the workflow worker runs
the checks, and a settled-in-full payout ends with a system proposal waiting for an operator.

Every subject is settled in full, so checks decide every case and no model is called. The report
reads only PostgreSQL timestamps, so it measures what the system recorded, not what the load
generator saw.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from onramp.audit.models import AuditEvent
from onramp.cases.models import SupportCase
from onramp.events.models import OutboxEvent, ProcessedEvent
from onramp.identity.models import Tenant, User
from onramp.identity.roles import Role
from onramp.marketplace.builder import WorldBuilder
from onramp.marketplace.models import MarketplaceTenant

LOAD_TENANT = "loadtest"
SERVICE_USER = f"{LOAD_TENANT}_{Role.MARKETPLACE_SERVICE.value}"
SIGNAL_GROUP = "onramp-case-signals"
PAYOUT_CENTS = 5_000


def subject_id(run_id: str, index: int) -> str:
    """The submission k6 names for its ``index``th intake. k6 builds the same string."""
    return f"{LOAD_TENANT}_{run_id}_s_{index}"


def intake_key(run_id: str, index: int) -> str:
    return f"{run_id}-{index}"


def ensure_tenant(onramp: sessionmaker[Session]) -> None:
    """The load tenant and its marketplace service user, created once."""
    with onramp() as session, session.begin():
        if session.get(Tenant, LOAD_TENANT) is not None:
            return
        session.add(Tenant(id=LOAD_TENANT, name="Load test", payout_approval_limit_cents=50_000))
        session.flush()
        session.add(
            User(
                id=SERVICE_USER,
                tenant_id=LOAD_TENANT,
                email=f"service@{LOAD_TENANT}.example.test",
                display_name="Load test marketplace service",
                role=Role.MARKETPLACE_SERVICE.value,
            )
        )


def seed_subjects(
    marketplace: sessionmaker[Session], run_id: str, count: int, now: datetime
) -> None:
    """``count`` submissions that were reviewed, paid and settled in full, two weeks ago."""
    start = now - timedelta(days=14)
    with marketplace() as session, session.begin():
        builder = WorldBuilder(session, LOAD_TENANT)
        if not _marketplace_tenant_exists(session):
            builder.tenant("Load test")
        for i in range(count):
            p = f"{LOAD_TENANT}_{run_id}_"
            builder.worker(f"{p}w_{i}", f"Load Worker {i}")
            builder.task(f"{p}t_{i}", PAYOUT_CENTS)
            builder.assignment(f"{p}a_{i}", f"{p}t_{i}", f"{p}w_{i}", start)
            builder.submission(subject_id(run_id, i), f"{p}a_{i}", start + timedelta(hours=2))
            builder.review(
                f"{p}qr_{i}", subject_id(run_id, i), "pass", "0.900", start + timedelta(hours=6)
            )
            builder.payment(
                f"{p}pay_{i}",
                f"{p}w_{i}",
                PAYOUT_CENTS,
                "settled",
                start + timedelta(hours=7),
                submission_id=subject_id(run_id, i),
            )
            builder.payment_event(
                f"{p}pe_{i}a", f"{p}pay_{i}", "authorized", PAYOUT_CENTS, start + timedelta(hours=7)
            )
            builder.payment_event(
                f"{p}pe_{i}b",
                f"{p}pay_{i}",
                "settlement_succeeded",
                PAYOUT_CENTS,
                start + timedelta(days=2),
            )
            if i % 500 == 499:
                session.flush()


def _marketplace_tenant_exists(session: Session) -> bool:
    return session.get(MarketplaceTenant, LOAD_TENANT) is not None


@dataclass(frozen=True)
class Distribution:
    count: int
    p50: float
    p95: float
    p99: float
    max: float

    @classmethod
    def of(cls, values: Sequence[float]) -> "Distribution | None":
        if not values:
            return None
        ordered = sorted(values)
        return cls(
            count=len(ordered),
            p50=percentile(ordered, 50),
            p95=percentile(ordered, 95),
            p99=percentile(ordered, 99),
            max=ordered[-1],
        )


def percentile(ordered: Sequence[float], p: float) -> float:
    """Nearest-rank percentile of an already sorted sequence."""
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def pipeline_report(onramp: sessionmaker[Session], run_id: str) -> dict[str, Any]:
    """What PostgreSQL recorded for one run's cases: how many reached a proposal, how long
    after intake, how long their ``case.created`` event waited for the publisher, and the
    throughput the whole pipeline sustained."""
    with onramp() as session:
        cases = session.execute(
            select(SupportCase.id, SupportCase.state, SupportCase.created_at).where(
                SupportCase.tenant_id == LOAD_TENANT,
                SupportCase.intake_key.like(f"{run_id}-%"),
            )
        ).all()
        case_ids = [row.id for row in cases]
        proposed = dict(
            session.execute(
                select(AuditEvent.subject_id, func.min(AuditEvent.occurred_at))
                .where(
                    AuditEvent.tenant_id == LOAD_TENANT,
                    AuditEvent.subject_id.in_(case_ids),
                    AuditEvent.action == "case.transitioned",
                    AuditEvent.details["to"].as_string() == "proposed",
                )
                .group_by(AuditEvent.subject_id)
            ).all()
        )
        published = session.execute(
            select(OutboxEvent.created_at, OutboxEvent.published_at).where(
                OutboxEvent.tenant_id == LOAD_TENANT,
                OutboxEvent.aggregate_id.in_(case_ids),
                OutboxEvent.event_type == "case.created",
            )
        ).all()

    created = {row.id: row.created_at for row in cases}
    to_proposal = [(proposed[c] - created[c]).total_seconds() for c in proposed]
    publish_lag = [
        (row.published_at - row.created_at).total_seconds()
        for row in published
        if row.published_at is not None
    ]
    states: dict[str, int] = {}
    for row in cases:
        states[str(row.state)] = states.get(str(row.state), 0) + 1
    throughput = None
    if proposed:
        window = (max(proposed.values()) - min(created.values())).total_seconds()
        throughput = len(proposed) / window if window > 0 else None
    return {
        "run_id": run_id,
        "cases": len(cases),
        "proposed": len(proposed),
        "states": states,
        "intake_to_proposal_s": _as_dict(Distribution.of(to_proposal)),
        "outbox_publish_lag_s": _as_dict(Distribution.of(publish_lag)),
        "unpublished_case_created": len(case_ids) - len(publish_lag),
        "proposals_per_s": throughput,
    }


def fallback_delivery(
    onramp: sessionmaker[Session], run_id: str, restarted_at: datetime | None = None
) -> dict[str, Any]:
    """Whether every ``case.created`` event of the run reached the case-signal consumer exactly
    once. Used to judge a broker outage: the events wait in the outbox while Kafka is down and
    must all arrive, once each, after it returns. With ``restarted_at``, also how long after the
    broker returned the last event created before then was published: the outage backlog, not
    the events that load kept producing afterwards."""
    with onramp() as session:
        case_ids = select(SupportCase.id).where(
            SupportCase.tenant_id == LOAD_TENANT, SupportCase.intake_key.like(f"{run_id}-%")
        )
        events = session.execute(
            select(OutboxEvent.event_id, OutboxEvent.created_at, OutboxEvent.published_at).where(
                OutboxEvent.tenant_id == LOAD_TENANT,
                OutboxEvent.event_type == "case.created",
                OutboxEvent.aggregate_id.in_(case_ids),
            )
        ).all()
        event_ids = [row.event_id for row in events]
        processed, distinct = session.execute(
            select(func.count(), func.count(func.distinct(ProcessedEvent.event_id))).where(
                ProcessedEvent.consumer_group == SIGNAL_GROUP,
                ProcessedEvent.event_id.in_(event_ids),
            )
        ).one()
    published = [row.published_at for row in events if row.published_at is not None]
    report: dict[str, Any] = {
        "events": len(event_ids),
        "published": len(published),
        "processed_by_signal_consumer": processed,
        "distinct_processed": distinct,
    }
    if restarted_at is not None:
        backlog = [row for row in events if row.created_at < restarted_at]
        cleared = [row.published_at for row in backlog if row.published_at is not None]
        report["backlog_events"] = len(backlog)
        report["backlog_published"] = len(cleared)
        if cleared and len(cleared) == len(backlog):
            report["backlog_cleared_after_restart_s"] = (
                max(cleared) - restarted_at
            ).total_seconds()
    return report


def _as_dict(distribution: Distribution | None) -> dict[str, float] | None:
    if distribution is None:
        return None
    return {
        "count": distribution.count,
        "p50": distribution.p50,
        "p95": distribution.p95,
        "p99": distribution.p99,
        "max": distribution.max,
    }
