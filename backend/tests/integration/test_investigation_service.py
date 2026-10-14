from collections.abc import Sequence

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.agent.graph import DEFAULT_BUDGET, Budget
from onramp.agent.model import Message, ModelResponse, ModelUnavailableError, ScriptedModel
from onramp.agent.models import InvestigationRun
from onramp.audit.service import Actor
from onramp.cases.models import CaseType, SupportCase
from onramp.cases.service import CaseService, IntakeRequest, transition
from onramp.cases.state import CaseState
from onramp.identity.principal import Principal
from onramp.identity.roles import Role
from onramp.investigations.service import InvestigationService, NotInvestigatingError
from onramp.marketplace.reader import SqlMarketplaceReader
from onramp.proposals.models import Proposal
from onramp.workflows.activities import AgentActivities, InvestigationUnavailableError
from onramp.workflows.contracts import CaseWorkflowInput
from tests.integration.agent_support import T0, DatabaseRetriever, World
from tests.integration.api_support import move_case
from tests.integration.test_agent_graph import GATHER, submit

SERVICE = Principal("acme", "svc", Role.MARKETPLACE_SERVICE)


def _investigating_case(world: World) -> str:
    onramp, marketplace = world
    case = (
        CaseService(onramp, SqlMarketplaceReader(marketplace), lambda: T0)
        .open_case(
            SERVICE,
            IntakeRequest(
                case_type=CaseType.PAYOUT,
                subject_id="s_1",
                intake_key="k",
                description="I was not paid",
            ),
        )
        .case
    )
    move_case(onramp, case.id, CaseState.INITIAL_CHECKS, CaseState.INVESTIGATING)
    return case.id


def _service(
    world: World, model: ScriptedModel, budget: Budget = DEFAULT_BUDGET
) -> InvestigationService:
    onramp, marketplace = world
    return InvestigationService(
        onramp, SqlMarketplaceReader(marketplace), DatabaseRetriever(onramp), model, budget=budget
    )


def _case(onramp: sessionmaker[Session], case_id: str) -> SupportCase:
    with onramp() as session:
        return session.get_one(SupportCase, case_id)


def test_resolution_becomes_a_pending_agent_proposal(world: World) -> None:
    case_id = _investigating_case(world)

    run = _service(world, ScriptedModel([*GATHER, submit()])).run("acme", case_id, Actor.system())

    assert run.status == "concluded"
    assert _case(world[0], case_id).state == CaseState.PROPOSED
    with world[0]() as session:
        proposal = session.get_one(Proposal, run.proposal_id)
        assert (proposal.proposer_type, proposal.status) == ("agent", "pending")
        assert "policy_passage:payout_v12_section_4_3" in proposal.evidence_refs
        stored = session.get_one(InvestigationRun, run.id)
        assert stored.prompt_version == "investigator-v1"
        assert len(stored.trajectory) >= 5


def test_escalating_conclusion_escalates_with_its_reason(world: World) -> None:
    case_id = _investigating_case(world)
    model = ScriptedModel(
        [
            *GATHER,
            submit(
                decision="escalate",
                resolution_code=None,
                proposed_action=None,
                escalation_reason="conflicting_evidence",
            ),
        ]
    )

    _service(world, model).run("acme", case_id, Actor.system())

    case = _case(world[0], case_id)
    assert (case.state, case.escalation_reason) == (CaseState.ESCALATED, "conflicting_evidence")


def test_exhausted_budget_escalates_as_failed_investigation(world: World) -> None:
    case_id = _investigating_case(world)
    model = ScriptedModel([GATHER[0]] * 10)

    run = _service(world, model, Budget(max_model_calls=3)).run("acme", case_id, Actor.system())

    assert run.status == "budget_exhausted"
    assert _case(world[0], case_id).escalation_reason == "investigation_failed"


def test_provider_failure_leaves_the_case_to_retry(world: World) -> None:
    case_id = _investigating_case(world)

    def down(_: Sequence[Message]) -> ModelResponse:
        raise ModelUnavailableError("503")

    run = _service(world, ScriptedModel([down])).run("acme", case_id, Actor.system())

    assert run.status == "model_error"
    assert _case(world[0], case_id).state == CaseState.INVESTIGATING


def test_agent_activity_raises_on_provider_failure_so_temporal_backs_off(world: World) -> None:
    onramp, _ = world
    case_id = _investigating_case(world)

    def down(_: Sequence[Message]) -> ModelResponse:
        raise ModelUnavailableError("503")

    activities = AgentActivities(onramp, _service(world, ScriptedModel([down])))

    with pytest.raises(InvestigationUnavailableError, match="503"):
        activities.run_investigation(CaseWorkflowInput("acme", case_id))
    assert _case(onramp, case_id).state == CaseState.INVESTIGATING


def test_operator_acting_mid_investigation_supersedes_the_run(world: World) -> None:
    onramp, _ = world
    case_id = _investigating_case(world)

    def operator_escalates_meanwhile(_: Sequence[Message]) -> ModelResponse:
        with onramp() as session, session.begin():
            case = session.get_one(SupportCase, case_id)
            case.escalation_reason = "operator_judgment"
            transition(
                session,
                case,
                CaseState.ESCALATED,
                actor=Actor.user("usr_op"),
                occurred_at=T0,
                reason="operator_judgment",
            )
        return submit()

    run = _service(world, ScriptedModel([*GATHER, operator_escalates_meanwhile])).run(
        "acme", case_id, Actor.system()
    )

    assert run.status == "superseded"
    assert _case(onramp, case_id).state == CaseState.ESCALATED
    with onramp() as session:
        assert session.scalars(select(Proposal)).all() == []


def test_only_cases_under_investigation_are_investigated(world: World) -> None:
    onramp, marketplace = world
    case = (
        CaseService(onramp, SqlMarketplaceReader(marketplace), lambda: T0)
        .open_case(
            SERVICE,
            IntakeRequest(
                case_type=CaseType.PAYOUT, subject_id="s_1", intake_key="k2", description="x"
            ),
        )
        .case
    )

    with pytest.raises(NotInvestigatingError):
        _service(world, ScriptedModel([])).run("acme", case.id, Actor.system())


def test_a_conclusion_that_cannot_become_a_proposal_escalates_instead_of_crashing(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from onramp.proposals.service import InvalidProposalError

    case_id = _investigating_case(world)

    def refuse(_: object) -> None:
        raise InvalidProposalError("does not fit the catalogue")

    monkeypatch.setattr("onramp.investigations.service.to_proposal_request", refuse)

    run = _service(world, ScriptedModel([*GATHER, submit()])).run("acme", case_id, Actor.system())

    assert run.status == "invalid_output"
    assert any("not a valid proposal" in p for p in run.problems)
    case = _case(world[0], case_id)
    assert (case.state, case.escalation_reason) == (CaseState.ESCALATED, "investigation_failed")
