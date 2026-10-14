from typing import Any

import pytest
from pydantic import ValidationError

from onramp.agent.conclusion import (
    AgentConclusion,
    Observations,
    to_proposal_request,
    validate_conclusion,
)
from onramp.cases.models import CaseType

SEEN = Observations(
    record_refs={"submission:s_1", "payment:pay_1", "payment_event:pe_2"},
    passage_ids={"payout_v12_section_4_3", "payout_v12_section_5_1"},
)


def _conclusion(**overrides: Any) -> AgentConclusion:
    values: dict[str, Any] = {
        "decision": "resolve",
        "resolution_code": "MISSING_PAYOUT",
        "confidence": 0.9,
        "reason_codes": ["SETTLEMENT_FAILED"],
        "citations": ["payout_v12_section_4_3"],
        "evidence_refs": ["payment_event:pe_2"],
        "proposed_action": {
            "action_type": "PAYOUT_ADJUSTMENT",
            "amount_cents": 8400,
            "currency": "USD",
        },
        "rationale": "Settlement failed and the window has passed.",
    }
    values.update(overrides)
    return AgentConclusion.model_validate(values)


def test_grounded_conclusion_passes() -> None:
    assert validate_conclusion(_conclusion(), CaseType.PAYOUT, SEEN) == []


def test_citation_not_retrieved_is_rejected() -> None:
    problems = validate_conclusion(
        _conclusion(citations=["payout_v12_section_9_9"]), CaseType.PAYOUT, SEEN
    )

    assert any("citations not retrieved" in p for p in problems)


def test_stale_version_citation_is_rejected_when_not_retrieved() -> None:
    problems = validate_conclusion(
        _conclusion(citations=["payout_v11_section_4_3"]), CaseType.PAYOUT, SEEN
    )

    assert problems


def test_record_not_read_is_rejected() -> None:
    problems = validate_conclusion(
        _conclusion(evidence_refs=["payment:pay_of_another_worker"]), CaseType.PAYOUT, SEEN
    )

    assert any("not read" in p for p in problems)


@pytest.mark.parametrize("field", ["worker_id", "tenant_id", "account_id", "payee"])
def test_identity_fields_are_not_part_of_the_schema(field: str) -> None:
    with pytest.raises(ValidationError):
        _conclusion(**{field: "w_999"})


def test_identity_cannot_hide_in_the_action() -> None:
    with pytest.raises(ValidationError):
        _conclusion(
            proposed_action={
                "action_type": "PAYOUT_ADJUSTMENT",
                "amount_cents": 1,
                "currency": "USD",
                "worker_id": "w_999",
            }
        )


def test_resolution_must_carry_its_required_action() -> None:
    problems = validate_conclusion(_conclusion(proposed_action=None), CaseType.PAYOUT, SEEN)

    assert any("requires action PAYOUT_ADJUSTMENT" in p for p in problems)


def test_wrong_case_type_resolution_is_rejected() -> None:
    problems = validate_conclusion(
        _conclusion(resolution_code="REVIEW_OVERTURNED", proposed_action=None),
        CaseType.PAYOUT,
        SEEN,
    )

    assert problems == ["REVIEW_OVERTURNED is not a payout resolution"]


def test_uncited_resolution_is_rejected() -> None:
    problems = validate_conclusion(_conclusion(citations=[]), CaseType.PAYOUT, SEEN)

    assert any("must cite" in p for p in problems)


def test_escalation_needs_a_reason_and_no_action() -> None:
    problems = validate_conclusion(
        _conclusion(decision="escalate", resolution_code=None), CaseType.PAYOUT, SEEN
    )

    assert "escalation requires an escalation_reason" in problems
    assert "an escalation cannot propose an action" in problems


def test_malformed_output_is_rejected_by_the_schema() -> None:
    with pytest.raises(ValidationError):
        AgentConclusion.model_validate({"decision": "pay them", "confidence": 3})


def test_validated_conclusion_maps_to_a_proposal_with_passage_refs() -> None:
    request = to_proposal_request(_conclusion())

    assert request.action_params == {"amount_cents": 8400, "currency": "USD"}
    assert "policy_passage:payout_v12_section_4_3" in request.evidence_refs


def test_prompt_versions_are_explicit_and_unknown_versions_fail() -> None:
    from onramp.agent.prompts import (
        DEFAULT_PROMPT_VERSION,
        UnknownPromptVersionError,
        system_prompt,
    )

    assert "untrusted_text" in system_prompt(CaseType.PAYOUT, DEFAULT_PROMPT_VERSION)
    with pytest.raises(UnknownPromptVersionError):
        system_prompt(CaseType.PAYOUT, "investigator-v99")


def test_every_prompt_renders_for_every_case_type_and_v2_extends_v1() -> None:
    from onramp.agent.prompts import PROMPTS, system_prompt

    for version in PROMPTS:
        for case_type in CaseType:
            assert "submit_conclusion" in system_prompt(case_type, version)
    for case_type in CaseType:
        assert system_prompt(case_type, "investigator-v2").startswith(
            system_prompt(case_type).rsplit("\n\n", 1)[0]
        )
