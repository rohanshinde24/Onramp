"""Spans and metrics on the consequential paths record what happened, not what was tried."""

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from onramp.actions.gateway import GatewayRejectedError
from onramp.approvals.models import Decision
from onramp.approvals.service import AlreadyDecidedError, ApprovalService
from onramp.marketplace.faults import Fault, FaultPlan, FaultyMarketplaceWriter
from onramp.marketplace.writer import SqlMarketplaceWriter
from onramp.proposals.models import Proposal
from onramp.telemetry.setup import instrument_databases
from tests.conftest import Captured
from tests.integration.test_action_gateway import (
    LEAD,
    NOW,
    Sessions,
    _approved_payout,
    _gateway,
    sessions,  # noqa: F401 - fixture
)


@pytest.fixture(autouse=True)
def _sdk_meter(telemetry: Captured) -> None:
    # Another suite may have installed a provider that does not export to the test reader.
    if not isinstance(metrics.get_meter_provider(), MeterProvider):
        pytest.skip("global meter provider is not the SDK provider")


def test_an_approval_and_its_transition_are_counted_once_committed(
    sessions: Sessions,  # noqa: F811
    telemetry: Captured,
) -> None:
    approvals = telemetry.total("onramp.approvals.decisions", decision="approved")
    to_executing = telemetry.total("onramp.case.transitions", to="executing")

    _approved_payout(sessions)

    assert telemetry.total("onramp.approvals.decisions", decision="approved") == approvals + 1
    assert telemetry.total("onramp.case.transitions", to="executing") == to_executing + 1


def test_refusals_count_nothing_and_are_labelled_on_the_span(
    sessions: Sessions,  # noqa: F811
    telemetry: Captured,
) -> None:
    onramp, _ = sessions
    _, proposal_id = _approved_payout(sessions)
    with onramp() as session:
        proposal_hash = session.get_one(Proposal, proposal_id).content_hash
    approvals = telemetry.total("onramp.approvals.decisions")

    with pytest.raises(AlreadyDecidedError):
        ApprovalService(onramp, lambda: NOW).decide(
            LEAD, proposal_id, Decision.APPROVED, content_hash=proposal_hash, note="again"
        )
    with pytest.raises(GatewayRejectedError):
        _gateway(sessions).execute("acme", "prop_missing")

    assert telemetry.total("onramp.approvals.decisions") == approvals
    (execute,) = [s for s in telemetry.spans.get_finished_spans() if s.name == "gateway.execute"]
    assert execute.attributes is not None
    assert execute.attributes["onramp.gateway.outcome"] == "rejected"


def test_a_lost_marketplace_response_shows_as_two_write_attempts(
    sessions: Sessions,  # noqa: F811
    telemetry: Captured,
) -> None:
    _, marketplace = sessions
    _, proposal_id = _approved_payout(sessions)
    flaky = FaultyMarketplaceWriter(
        SqlMarketplaceWriter(marketplace, lambda: NOW),
        FaultPlan().inject("issue_adjustment", 1, Fault.LOST_RESPONSE),
    )
    unknown = telemetry.total("onramp.marketplace.write.attempts", result="outcome_unknown")

    action = _gateway(sessions, writer=flaky).execute("acme", proposal_id)

    assert action.status == "succeeded"
    assert (
        telemetry.total("onramp.marketplace.write.attempts", result="outcome_unknown")
        == unknown + 1
    )
    writes = [s for s in telemetry.spans.get_finished_spans() if s.name == "marketplace.write"]
    assert [s.attributes["onramp.attempt"] for s in writes if s.attributes] == [1, 2]
    (execute,) = [s for s in telemetry.spans.get_finished_spans() if s.name == "gateway.execute"]
    assert all(w.parent is not None and w.parent.span_id == execute.context.span_id for w in writes)
    assert execute.attributes is not None
    assert execute.attributes["onramp.gateway.outcome"] == "succeeded"


def test_sql_statements_become_spans_inside_the_current_trace(
    onramp_sessions: sessionmaker[Session], telemetry: Captured
) -> None:
    instrument_databases(onramp_sessions.kw["bind"].engine)

    with (
        trace.get_tracer("test").start_as_current_span("unit of work") as parent,
        onramp_sessions() as session,
    ):
        session.execute(text("SELECT 1"))

    statements = [
        s
        for s in telemetry.spans.get_finished_spans()
        if s.name.startswith("SELECT") and s.context.trace_id == parent.get_span_context().trace_id
    ]
    assert statements
    assert statements[0].attributes is not None
    assert statements[0].attributes["db.system"] == "postgresql"
    assert "SELECT 1" not in str(statements[0].attributes)  # no statement text


def test_a_failing_statement_ends_its_span_as_an_error(
    onramp_sessions: sessionmaker[Session], telemetry: Captured
) -> None:
    instrument_databases(onramp_sessions.kw["bind"].engine)

    with onramp_sessions() as session, pytest.raises(ProgrammingError):
        session.execute(text("SELECT * FROM no_such_table"))

    (failed,) = [
        s
        for s in telemetry.spans.get_finished_spans()
        if s.name.startswith("SELECT") and not s.status.is_ok
    ]
    assert failed.status.description == "UndefinedTable"  # the driver error
