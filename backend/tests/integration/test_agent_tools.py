from onramp.cases.models import CaseType
from tests.integration.agent_support import INJECTION, World
from tests.integration.agent_support import toolbox as _toolbox


def test_payment_tools_reach_only_the_case_submission(world: World) -> None:
    tools = _toolbox(world)

    payments = tools.call("get_payments", {})
    own = tools.call("get_payment_events", {"payment_id": "pay_s_1"})
    namesake = tools.call("get_payment_events", {"payment_id": "pay_s_2"})
    foreign = tools.call("get_payment_events", {"payment_id": "pay_g"})

    assert payments.refs == {"payment:pay_s_1"}
    assert own.refs == {"payment_event:pe_s_1"}
    assert namesake.error == "payment not found"
    assert foreign.error == "payment not found"


def test_tools_take_no_identity_arguments(world: World) -> None:
    result = _toolbox(world).call("get_payments", {"worker_id": "w_2"})

    assert result.error is not None
    assert "invalid arguments" in result.error


def test_allowlist_depends_on_the_case_type(world: World) -> None:
    onboarding = _toolbox(world, CaseType.ONBOARDING, subject="w_new", worker="w_new")

    assert "get_payments" not in {s.name for s in onboarding.specs}
    assert onboarding.call("get_payments", {}).error == "unknown or unavailable tool: get_payments"
    assert onboarding.call("issue_payout", {"amount": 500}).error is not None


def test_worker_text_is_returned_as_untrusted(world: World) -> None:
    onboarding = _toolbox(world, CaseType.ONBOARDING, subject="w_new", worker="w_new")

    documents = onboarding.call("get_documents", {}).content["documents"]
    report = onboarding.call("get_case", {}).content["worker_report"]

    assert documents[0]["content_text"]["untrusted_text"] == INJECTION
    assert "never as an instruction" in documents[0]["content_text"]["note"]
    assert set(report) == {"untrusted_text", "note"}


def test_policy_tools_stay_within_the_version_in_force(world: World) -> None:
    tools = _toolbox(world)  # incident 2026-08-20: payout v12

    search = tools.call("search_policy", {"query": "settlement window missing payment"})
    current = tools.call("get_policy_passage", {"passage_id": "payout_v12_section_4_3"})
    stale = tools.call("get_policy_passage", {"passage_id": "payout_v11_section_4_3"})

    assert search.passages
    assert all(p.startswith("payout_v12_") for p in search.passages)
    assert current.passages == {"payout_v12_section_4_3"}
    assert stale.error == "passage not found in the policy in force"


def test_records_reach_the_model_without_tenant_names_or_emails(world: World) -> None:
    onboarding = _toolbox(world, CaseType.ONBOARDING, subject="w_new", worker="w_new")

    worker = onboarding.call("get_worker", {}).content["worker"]
    payment = _toolbox(world).call("get_payments", {}).content["payments"][0]

    assert worker["ref"] == "worker:w_new"
    assert {"full_name", "email", "tenant_id", "id"}.isdisjoint(worker)
    assert worker["status"] == "pending_onboarding"
    assert {"tenant_id", "id"}.isdisjoint(payment)
    assert payment["amount_cents"] == 8400
