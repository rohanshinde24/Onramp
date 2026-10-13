"""The evidence path over real Kafka, including duplicate publication, consumer restart and
full replay. The case must resume exactly once."""

import os
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from onramp.audit.models import AuditEvent
from onramp.cases.evidence_consumer import CaseEvidenceHandler
from onramp.cases.models import CaseType, SupportCase
from onramp.cases.service import CaseService, IntakeRequest
from onramp.cases.state import CaseState
from onramp.events.consumer import ConsumerRunner, Outcome
from onramp.events.kafka import (
    KafkaDeadLetterSink,
    KafkaMessageSource,
    KafkaSink,
    consumer_config,
    ensure_topics,
    producer_config,
)
from onramp.events.publisher import OutboxPublisher
from onramp.events.topics import all_topic_names
from onramp.identity.models import Tenant
from onramp.identity.principal import Principal
from onramp.identity.roles import Role
from onramp.marketplace.activity import MarketplaceActivity
from onramp.marketplace.builder import WorldBuilder
from onramp.marketplace.models import MarketplaceOutboxEvent
from onramp.marketplace.reader import SqlMarketplaceReader
from tests.integration.api_support import move_case

pytestmark = pytest.mark.kafka
BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092")


def _run_consumer(
    onramp: sessionmaker[Session],
    marketplace: sessionmaker[Session],
    group: str,
    seconds: float,
) -> ConsumerRunner:
    handler = CaseEvidenceHandler(SqlMarketplaceReader(marketplace))
    handler.group = group  # isolate from other runs of this test
    source = KafkaMessageSource(consumer_config(BOOTSTRAP, group, "test"), handler.topics)
    runner = ConsumerRunner(
        onramp,
        source,
        KafkaDeadLetterSink(producer_config(BOOTSTRAP, "test-dlq", 10_000)),
        handler,
    )
    stop = threading.Event()
    timer = threading.Timer(seconds, stop.set)
    timer.start()
    runner.run_until_stopped(stop, poll_timeout_s=0.2)
    return runner


def _transitions_to_investigating(onramp: sessionmaker[Session], case_id: str) -> int:
    with onramp() as session:
        events = session.scalars(
            select(AuditEvent).filter_by(subject_id=case_id, action="case.transitioned")
        )
        return sum(1 for e in events if e.details["to"] == "investigating")


def test_evidence_resumes_case_once_despite_duplicates_restart_and_replay(
    onramp_committed: sessionmaker[Session], marketplace_committed: sessionmaker[Session]
) -> None:
    ensure_topics(BOOTSTRAP, all_topic_names(), partitions=3, replication_factor=1)
    tenant = f"t{uuid.uuid4().hex[:8]}"
    worker = f"{tenant}_w"
    with marketplace_committed() as session, session.begin():
        WorldBuilder(session, tenant).tenant().worker(
            worker, "Ana Diaz", status="pending_onboarding"
        )
    with onramp_committed() as session, session.begin():
        session.add(Tenant(id=tenant, name=tenant, payout_approval_limit_cents=1))
    cases = CaseService(onramp_committed, SqlMarketplaceReader(marketplace_committed))
    case_id = cases.open_case(
        Principal(tenant, "svc", Role.MARKETPLACE_SERVICE),
        IntakeRequest(
            case_type=CaseType.ONBOARDING, subject_id=worker, intake_key="k", description="stuck"
        ),
    ).case.id
    move_case(onramp_committed, case_id, CaseState.INITIAL_CHECKS, CaseState.WAITING_FOR_EVIDENCE)

    MarketplaceActivity(marketplace_committed).upload_document(tenant, worker, "tax_form", "W-9")
    relay = OutboxPublisher(
        marketplace_committed,
        KafkaSink(producer_config(BOOTSTRAP, "test-relay", 10_000)),
        outbox=MarketplaceOutboxEvent,
        clock=lambda: datetime.now(UTC) + timedelta(seconds=1),
    )
    assert relay.run_once().published >= 1

    # Simulate the relay crashing after Kafka acked: the row is unmarked and sent again.
    with marketplace_committed() as session, session.begin():
        session.execute(
            update(MarketplaceOutboxEvent)
            .where(MarketplaceOutboxEvent.tenant_id == tenant)
            .values(published_at=None)
        )
    assert relay.run_once().published >= 1

    group = f"test-evidence-{uuid.uuid4().hex[:8]}"
    first = _run_consumer(onramp_committed, marketplace_committed, group, seconds=8)
    # Restart: a new consumer in the same group resumes from committed offsets.
    _run_consumer(onramp_committed, marketplace_committed, group, seconds=3)
    # Replay: a different group reads the whole topic again, with its own dedupe state.
    _run_consumer(onramp_committed, marketplace_committed, f"{group}-replay", seconds=8)

    with onramp_committed() as session:
        assert session.get_one(SupportCase, case_id).state == CaseState.INVESTIGATING
    assert first.stats.counts[Outcome.DUPLICATE] >= 1  # the republished copy was recognised
    # Even the replay group, which saw the event as new, found the case no longer waiting.
    assert _transitions_to_investigating(onramp_committed, case_id) == 1
