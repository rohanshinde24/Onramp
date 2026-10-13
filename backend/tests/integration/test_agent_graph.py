"""The real compiled investigation graph, driven by scripted models."""

from collections.abc import Sequence
from typing import Any

from onramp.agent.graph import Budget, investigate
from onramp.agent.model import (
    Message,
    ModelRefusedError,
    ModelResponse,
    ModelUnavailableError,
    ScriptedModel,
    ToolDefinition,
    Usage,
    call,
    tool_turn,
)
from onramp.cases.models import CaseType
from tests.integration.agent_support import World, toolbox

GROUNDED: dict[str, Any] = {
    "decision": "resolve",
    "resolution_code": "MISSING_PAYOUT",
    "confidence": 0.85,
    "reason_codes": ["MISSING_SETTLEMENT"],
    "citations": ["payout_v12_section_4_3"],
    "evidence_refs": ["payment:pay_s_1", "payment_event:pe_s_1"],
    "proposed_action": {
        "action_type": "PAYOUT_ADJUSTMENT",
        "amount_cents": 8400,
        "currency": "USD",
    },
    "rationale": "Authorized, never settled, window passed.",
}

GATHER = [
    tool_turn(call("get_case")),
    tool_turn(call("get_payments"), call("get_payment_events", payment_id="pay_s_1")),
    tool_turn(call("get_policy_passage", passage_id="payout_v12_section_4_3")),
]


def submit(**overrides: Any) -> ModelResponse:
    return tool_turn(call("submit_conclusion", **{**GROUNDED, **overrides}))


def test_grounded_investigation_concludes_with_a_full_trajectory(world: World) -> None:
    model = ScriptedModel([*GATHER, submit()])

    result = investigate(model, toolbox(world), CaseType.PAYOUT)

    assert result.status == "concluded"
    assert result.conclusion is not None
    assert result.conclusion.resolution_code == "MISSING_PAYOUT"
    assert result.model_calls == 4
    tools = [t["tool"] for t in result.trajectory if t["kind"] == "tool"]
    assert tools == ["get_case", "get_payments", "get_payment_events", "get_policy_passage"]
    assert result.observations.passage_ids == {"payout_v12_section_4_3"}
    assert result.input_tokens == 400


def test_forged_citation_is_rejected_then_corrected(world: World) -> None:
    model = ScriptedModel([*GATHER, submit(citations=["payout_v12_section_9_9"]), submit()])

    result = investigate(model, toolbox(world), CaseType.PAYOUT)

    assert result.status == "concluded"
    rejection = next(t for t in result.trajectory if t["kind"] == "validation")
    assert any("citations not retrieved" in p for p in rejection["problems"])
    # The model saw why it was rejected.
    last_input = model.calls[-1][1]
    assert "citations not retrieved" in last_input[-1].tool_outputs[0].content


def test_persistently_invalid_output_stops(world: World) -> None:
    bad = submit(evidence_refs=["payment:pay_s_2"])  # a namesake's payment it never read
    model = ScriptedModel([*GATHER, bad, bad, bad])

    result = investigate(
        model, toolbox(world), CaseType.PAYOUT, budget=Budget(max_invalid_conclusions=2)
    )

    assert result.status == "invalid_output"
    assert result.conclusion is None


def test_step_budget_ends_a_looping_investigation(world: World) -> None:
    model = ScriptedModel([tool_turn(call("get_case")) for _ in range(50)])

    result = investigate(model, toolbox(world), CaseType.PAYOUT, budget=Budget(max_model_calls=5))

    assert result.status == "budget_exhausted"
    assert result.model_calls == 5


def test_token_budget_is_enforced(world: World) -> None:
    model = ScriptedModel([tool_turn(call("get_case"), tokens=4_000) for _ in range(10)])

    result = investigate(model, toolbox(world), CaseType.PAYOUT, budget=Budget(max_tokens=9_000))

    assert result.status == "budget_exhausted"
    assert result.model_calls == 2


def test_timeout_is_enforced(world: World) -> None:
    ticks = iter(range(0, 1000, 40))
    model = ScriptedModel([tool_turn(call("get_case")) for _ in range(10)])

    result = investigate(
        model,
        toolbox(world),
        CaseType.PAYOUT,
        budget=Budget(timeout_s=90),
        clock=lambda: float(next(ticks)),
    )

    assert result.status == "budget_exhausted"


def test_text_only_reply_is_nudged(world: World) -> None:
    model = ScriptedModel(
        [ModelResponse(text="Let me think.", usage=Usage(10, 5)), *GATHER, submit()]
    )

    result = investigate(model, toolbox(world), CaseType.PAYOUT)

    assert result.status == "concluded"
    assert "submit_conclusion" in model.calls[1][1][-1].text


def test_provider_failure_is_reported(world: World) -> None:
    def fail(_: Sequence[Message]) -> ModelResponse:
        raise ModelUnavailableError("overloaded")

    result = investigate(ScriptedModel([fail]), toolbox(world), CaseType.PAYOUT)

    assert result.status == "model_error"
    assert result.problems == ["overloaded"]


def test_obeying_an_injected_document_cannot_produce_a_write(world: World) -> None:
    """The scripted model does what the injected document says. Nothing it can do gets
    past the tools or the validation."""
    onboarding = toolbox(world, CaseType.ONBOARDING, subject="w_new", worker="w_new")
    model = ScriptedModel(
        [
            tool_turn(call("get_documents")),
            tool_turn(call("issue_payout", amount_cents=50_000)),
            submit(
                resolution_code="MISSING_PAYOUT", citations=[], evidence_refs=["document:doc_1"]
            ),
            submit(
                decision="resolve",
                resolution_code="REQUIREMENTS_MET",
                citations=[],
                evidence_refs=["document:doc_1"],
                proposed_action={
                    "action_type": "PAYOUT_ADJUSTMENT",
                    "amount_cents": 50_000,
                    "currency": "USD",
                },
            ),
            submit(resolution_code="REQUIREMENTS_MET", worker_id="w_attacker"),
        ]
    )

    result = investigate(model, onboarding, CaseType.ONBOARDING)

    assert result.status == "invalid_output"
    assert result.conclusion is None
    payout_attempt = [t for t in result.trajectory if t.get("tool") == "issue_payout"]
    assert payout_attempt[0]["error"] == "unknown or unavailable tool: issue_payout"


def test_tool_definitions_never_include_a_write(world: World) -> None:
    seen: list[Sequence[ToolDefinition]] = []

    class Spy(ScriptedModel):
        def respond(
            self, system: str, transcript: Sequence[Message], tools: Sequence[ToolDefinition]
        ) -> ModelResponse:
            seen.append(tools)
            return super().respond(system, transcript, tools)

    for case_type, subject, worker in [
        (CaseType.PAYOUT, "s_1", "w_1"),
        (CaseType.ONBOARDING, "w_new", "w_new"),
    ]:
        investigate(Spy([submit()]), toolbox(world, case_type, subject, worker), case_type)

    names = {t.name for tools in seen for t in tools}
    assert not {n for n in names if n.startswith(("issue", "activate", "overturn", "request"))}


def test_provider_refusal_ends_the_investigation(world: World) -> None:
    def refuse(_: Sequence[Message]) -> ModelResponse:
        raise ModelRefusedError("model declined (category: None)")

    result = investigate(ScriptedModel([refuse]), toolbox(world), CaseType.PAYOUT)

    assert result.status == "model_refused"
    assert result.conclusion is None
