"""Spans and metrics on the consequential paths record what happened, not what was tried."""

import pytest
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider

from onramp.actions.gateway import GatewayRejectedError
from onramp.approvals.models import Decision
from onramp.approvals.service import AlreadyDecidedError, ApprovalService
from onramp.marketplace.faults import Fault, FaultPlan, FaultyMarketplaceWriter
from onramp.marketplace.writer import SqlMarketplaceWriter
from onramp.proposals.models import Proposal
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
