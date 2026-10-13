"""Checks over the demo world, loaded through the real reader and policy tables."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.cases.models import SupportCase
from onramp.cases.resolutions import ActionType, ResolutionCode
from onramp.checks.outcome import CheckOutcome, OutcomeKind
from onramp.checks.runner import run_checks
from onramp.entrypoints.seed import seed
from onramp.marketplace.reader import SqlMarketplaceReader
from onramp.policy.applicability import NoApplicablePolicyError
from onramp.policy.corpus import load_corpus
from onramp.policy.loader import load_documents

NOW = datetime(2026, 10, 1, tzinfo=UTC)
CORPUS = Path(__file__).resolve().parents[3] / "policies"


@pytest.fixture
def world(
    onramp_sessions: sessionmaker[Session], marketplace_sessions: sessionmaker[Session]
) -> tuple[sessionmaker[Session], SqlMarketplaceReader]:
    seed(onramp_sessions, marketplace_sessions, NOW)
    with onramp_sessions() as session, session.begin():
        load_documents(session, load_corpus(CORPUS))
    return onramp_sessions, SqlMarketplaceReader(marketplace_sessions)


World = tuple[sessionmaker[Session], SqlMarketplaceReader]


def _check(world: World, subject: str) -> CheckOutcome:
    sessions, reader = world
    with sessions() as session:
        case = session.scalars(
            select(SupportCase).filter_by(tenant_id="acme", subject_id=subject)
        ).one()
        return run_checks(session, case, reader, NOW)


def test_failed_settlement_is_missing_payout(world: World) -> None:
    outcome = _check(world, "acme_s_8821")

    assert outcome.resolution_code is ResolutionCode.MISSING_PAYOUT
    assert outcome.action_params == {"amount_cents": 8400, "currency": "USD"}
    assert "payment_event:acme_pe_9202b" in outcome.evidence_refs
    assert "payout_v12_section_4_3" in outcome.citations


def test_settled_payment_is_paid_in_full(world: World) -> None:
    assert _check(world, "acme_s_8822").resolution_code is ResolutionCode.PAID_IN_FULL


def test_inapplicable_formatting_score_overturns_review(world: World) -> None:
    outcome = _check(world, "acme_qr_4283")

    assert outcome.resolution_code is ResolutionCode.REVIEW_OVERTURNED
    assert outcome.action_type is ActionType.OVERTURN_QUALITY_REVIEW


def test_missing_tax_form_waits_for_evidence(world: World) -> None:
    outcome = _check(world, "acme_w_610")

    assert outcome.kind is OutcomeKind.WAIT_FOR_EVIDENCE
    assert outcome.missing_items == ("tax_form",)


def test_no_policy_in_force_is_an_error_not_a_guess(
    world: World,
) -> None:
    sessions, reader = world
    with sessions() as session:
        case = session.scalars(
            select(SupportCase).filter_by(tenant_id="acme", subject_id="acme_s_8821")
        ).one()
        case.incident_at = datetime(2025, 1, 1, tzinfo=UTC)  # before any payout version

        with pytest.raises(NoApplicablePolicyError):
            run_checks(session, case, reader, NOW)
