from datetime import datetime

from onramp.cases.resolutions import ActionType, ResolutionCode
from onramp.checks.onboarding import OnboardingFacts, check_onboarding
from onramp.checks.outcome import OutcomeKind
from onramp.marketplace.records import DocumentRecord, VerificationRecordView, WorkerRecord
from tests.unit.checks.support import T0, days, policy

JULY = datetime.fromisoformat("2026-07-15T12:00:00+00:00")  # onboarding v3
SEPT = datetime.fromisoformat("2026-09-10T12:00:00+00:00")  # onboarding v4


def _v(
    kind: str, status: str, completed: datetime | None = T0, vid: str | None = None
) -> VerificationRecordView:
    return VerificationRecordView(
        id=vid or f"v_{kind}",
        tenant_id="acme",
        worker_id="w_1",
        verification_kind=kind,
        status=status,
        completed_at=completed,
        expires_at=None,
    )


def facts(
    *verifications: VerificationRecordView,
    now: datetime = SEPT,
    version: int = 4,
    country: str = "US",
    status: str = "pending_onboarding",
    documents: tuple[DocumentRecord, ...] = (),
) -> OnboardingFacts:
    return OnboardingFacts(
        now=now,
        policy=policy("onboarding", version),
        worker=WorkerRecord(
            id="w_1",
            tenant_id="acme",
            full_name="Ana Diaz",
            email="a@x.test",
            status=status,
            country=country,
        ),
        verifications=verifications,
        documents=documents,
    )


IDENTITY = _v("identity", "passed")
SKILLS = _v("skills_assessment", "passed")


def test_all_requirements_met_proposes_activation() -> None:
    outcome = check_onboarding(facts(IDENTITY, SKILLS, _v("tax_form", "passed")))

    assert outcome.resolution_code is ResolutionCode.REQUIREMENTS_MET
    assert outcome.action_type is ActionType.ACTIVATE_WORKER
    assert "onboarding_v4_section_2_2" in outcome.citations


def test_tax_form_is_required_for_us_workers_only_from_v4() -> None:
    under_v3 = check_onboarding(facts(IDENTITY, SKILLS, now=JULY, version=3))
    under_v4 = check_onboarding(facts(IDENTITY, SKILLS))

    assert under_v3.resolution_code is ResolutionCode.REQUIREMENTS_MET
    assert under_v4.kind is OutcomeKind.WAIT_FOR_EVIDENCE
    assert under_v4.missing_items == ("tax_form",)


def test_non_us_worker_needs_no_tax_form_under_v4() -> None:
    outcome = check_onboarding(facts(IDENTITY, SKILLS, country="CA"))

    assert outcome.resolution_code is ResolutionCode.REQUIREMENTS_MET


def test_expired_identity_must_be_repeated() -> None:
    old = _v("identity", "passed", completed=SEPT - days(400))

    outcome = check_onboarding(facts(old, SKILLS, _v("tax_form", "passed")))

    assert outcome.kind is OutcomeKind.WAIT_FOR_EVIDENCE
    assert outcome.missing_items == ("identity",)
    assert "IDENTITY_EXPIRED" in outcome.reason_codes
    assert "onboarding_v4_section_3_1" in outcome.citations


def test_failed_identity_is_final() -> None:
    outcome = check_onboarding(facts(_v("identity", "failed"), SKILLS))

    assert outcome.resolution_code is ResolutionCode.REQUIREMENT_FAILED
    assert outcome.action_type is None
    assert "onboarding_v4_section_4_1" in outcome.citations


def test_newest_attempt_decides() -> None:
    retake = _v("skills_assessment", "passed", completed=T0 + days(40), vid="v_skills_2")
    first = _v("skills_assessment", "failed", completed=T0, vid="v_skills_1")

    outcome = check_onboarding(facts(IDENTITY, first, retake, _v("tax_form", "passed")))

    assert outcome.resolution_code is ResolutionCode.REQUIREMENTS_MET


def test_failed_tax_form_is_not_guessed() -> None:
    outcome = check_onboarding(facts(IDENTITY, SKILLS, _v("tax_form", "failed")))

    assert outcome.kind is OutcomeKind.INVESTIGATE


def test_uploaded_but_unverified_document_needs_investigation() -> None:
    upload = DocumentRecord(
        id="doc_1",
        tenant_id="acme",
        worker_id="w_1",
        document_kind="tax_form",
        content_text="W-9",
        uploaded_at=SEPT - days(1),
    )

    outcome = check_onboarding(facts(IDENTITY, SKILLS, documents=(upload,)))

    assert outcome.kind is OutcomeKind.INVESTIGATE
    assert "document:doc_1" in outcome.evidence_refs


def test_already_active_worker() -> None:
    outcome = check_onboarding(facts(status="active"))

    assert outcome.resolution_code is ResolutionCode.ALREADY_ACTIVE
