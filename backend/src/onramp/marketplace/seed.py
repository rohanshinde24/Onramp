"""Demo marketplace history for local development. Not evaluation data (that lives in eval/)."""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from onramp.marketplace import models as m
from onramp.marketplace.builder import WorldBuilder


def seed_marketplace(session: Session, tenant_id: str, start: datetime) -> bool:
    """Create the demo world for a tenant. Returns False if it already exists."""
    if session.scalar(select(m.MarketplaceTenant).filter_by(id=tenant_id)) is not None:
        return False
    w = WorldBuilder(session, tenant_id).tenant()
    p = f"{tenant_id}_"

    # Payout: accepted, authorized, settlement failed.
    w.worker(f"{p}w_492", "Sam Lee")
    w.worker(f"{p}w_493", "Sam Lee")  # namesake
    w.task(f"{p}t_8821", 8400)
    w.assignment(f"{p}a_8821", f"{p}t_8821", f"{p}w_492", start)
    w.submission(f"{p}s_8821", f"{p}a_8821", start + timedelta(hours=4))
    w.review(f"{p}qr_4281", f"{p}s_8821", "pass", "0.910", start + timedelta(hours=9))
    w.payment(
        f"{p}pay_9202",
        f"{p}w_492",
        8400,
        "failed",
        start + timedelta(hours=10),
        submission_id=f"{p}s_8821",
    )
    w.payment_event(f"{p}pe_9202a", f"{p}pay_9202", "authorized", 8400, start + timedelta(hours=10))
    w.payment_event(
        f"{p}pe_9202b",
        f"{p}pay_9202",
        "settlement_failed",
        8400,
        start + timedelta(days=3),
        failure_reason="bank_account_closed",
    )

    # Payout: settled in full.
    w.task(f"{p}t_8822", 5000)
    w.assignment(f"{p}a_8822", f"{p}t_8822", f"{p}w_493", start)
    w.submission(f"{p}s_8822", f"{p}a_8822", start + timedelta(hours=2))
    w.review(f"{p}qr_4282", f"{p}s_8822", "pass", "0.880", start + timedelta(hours=6))
    w.payment(
        f"{p}pay_9203",
        f"{p}w_493",
        5000,
        "settled",
        start + timedelta(hours=7),
        submission_id=f"{p}s_8822",
    )
    w.payment_event(f"{p}pe_9203a", f"{p}pay_9203", "authorized", 5000, start + timedelta(hours=7))
    w.payment_event(
        f"{p}pe_9203b", f"{p}pay_9203", "settlement_succeeded", 5000, start + timedelta(days=2)
    )

    # Quality review dispute: failed on a criterion that may not apply.
    w.task(f"{p}t_8823", 6000, task_type="transcription", criteria=["accuracy", "formatting"])
    w.assignment(f"{p}a_8823", f"{p}t_8823", f"{p}w_492", start)
    w.submission(f"{p}s_8823", f"{p}a_8823", start + timedelta(hours=5), status="rejected")
    w.review(
        f"{p}qr_4283",
        f"{p}s_8823",
        "fail",
        "0.550",
        start + timedelta(hours=8),
        rubric_scores={"accuracy": 0.95, "formatting": 0.15, "timestamps": "n/a"},
        comment="Timestamps missing.",
    )

    # Onboarding: identity passed, tax form missing.
    w.worker(f"{p}w_610", "Ana Diaz", status="pending_onboarding")
    w.verification(
        f"{p}v_610a", f"{p}w_610", "identity", "passed", completed_at=start - timedelta(days=2)
    )
    w.verification(
        f"{p}v_610b",
        f"{p}w_610",
        "skills_assessment",
        "passed",
        completed_at=start - timedelta(days=1),
    )
    return True
