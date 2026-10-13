"""Worker onboarding checks (onboarding policy, sections cited inline)."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from onramp.cases.resolutions import ActionType, ResolutionCode
from onramp.checks.outcome import CheckOutcome, Evidence, OutcomeKind
from onramp.checks.policy_context import PolicyContext
from onramp.marketplace.records import DocumentRecord, VerificationRecordView, WorkerRecord

_EPOCH = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class OnboardingFacts:
    now: datetime
    policy: PolicyContext
    worker: WorkerRecord
    verifications: tuple[VerificationRecordView, ...]
    documents: tuple[DocumentRecord, ...]


def check_onboarding(f: OnboardingFacts) -> CheckOutcome:
    e = Evidence()
    e.record(f.worker.ref)

    if f.worker.status == "active":
        e.reason("ALREADY_ACTIVE")
        e.note("Worker account is already active.")
        return e.outcome(OutcomeKind.RESOLVE, resolution_code=ResolutionCode.ALREADY_ACTIVE)

    required = list(f.policy.value("required_verifications"))
    e.cite(f.policy.parameter_passage("required_verifications"))
    if f.worker.country in f.policy.value("tax_form_required_countries"):
        required.append("tax_form")
        e.cite(f.policy.parameter_passage("tax_form_required_countries"))
        e.note(f"Tax form required for workers in {f.worker.country}.")
    validity = timedelta(days=int(f.policy.value("identity_validity_days")))

    missing: list[str] = []
    for kind in required:
        latest = _latest(f.verifications, kind)
        if latest is None or latest.status == "pending":
            missing.append(kind)
            e.note(f"{kind}: {'pending' if latest else 'not started'}.")
            if latest is not None:
                e.record(latest.ref)
            continue
        e.record(latest.ref)
        if latest.status == "failed":
            if kind == "tax_form":
                # The policy says nothing about a failed tax form.
                e.reason("TAX_FORM_FAILED_NOT_COVERED")
                e.note("Tax form verification failed; policy has no rule for this.")
                return e.outcome(OutcomeKind.INVESTIGATE)
            e.cite(f.policy.passage("4.1"))
            e.reason(f"{kind.upper()}_FAILED")
            e.note(f"{kind} verification failed.")
            return e.outcome(OutcomeKind.RESOLVE, resolution_code=ResolutionCode.REQUIREMENT_FAILED)
        expired = latest.status == "expired" or (
            kind == "identity"
            and latest.completed_at is not None
            and latest.completed_at + validity <= f.now
        )
        if expired:
            missing.append(kind)
            e.cite(f.policy.parameter_passage("identity_validity_days"))
            e.reason("IDENTITY_EXPIRED" if kind == "identity" else f"{kind.upper()}_EXPIRED")
            e.note(f"{kind} verification expired.")

    if missing:
        unreviewed = [
            d
            for d in f.documents
            if d.document_kind in missing and _after_latest(d, f.verifications)
        ]
        if unreviewed:
            # A worker sent something the marketplace has not verified yet. Reading it is
            # judgment, not a lookup.
            for document in unreviewed:
                e.record(document.ref)
            e.reason("UNVERIFIED_UPLOAD")
            e.note(f"Uploaded but not verified: {', '.join(d.document_kind for d in unreviewed)}.")
            return e.outcome(OutcomeKind.INVESTIGATE)
        e.reason("MISSING_REQUIREMENT")
        return e.outcome(OutcomeKind.WAIT_FOR_EVIDENCE, missing_items=tuple(missing))

    e.reason("REQUIREMENTS_MET")
    e.note(f"Passed: {', '.join(required)}.")
    return e.outcome(
        OutcomeKind.RESOLVE,
        resolution_code=ResolutionCode.REQUIREMENTS_MET,
        action_type=ActionType.ACTIVATE_WORKER,
    )


def _latest(
    verifications: tuple[VerificationRecordView, ...], kind: str
) -> VerificationRecordView | None:
    of_kind = [v for v in verifications if v.verification_kind == kind]
    if not of_kind:
        return None
    # Undated (pending) records sort last: they are the newest attempt.
    return max(of_kind, key=lambda v: (v.completed_at is None, v.completed_at or _EPOCH, v.id))


def _after_latest(
    document: DocumentRecord, verifications: tuple[VerificationRecordView, ...]
) -> bool:
    latest = _latest(verifications, document.document_kind)
    return (
        latest is None or latest.completed_at is None or document.uploaded_at > latest.completed_at
    )
