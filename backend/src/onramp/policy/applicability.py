"""Which policy version governs a case: the one in force at the incident time, never simply
the latest."""

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from onramp.policy.models import Policy, PolicyVersion


class NoApplicablePolicyError(LookupError):
    """No version of the policy was in force at that time. Checks must escalate, not guess."""


def applicable_version(session: Session, policy_id: str, at: datetime) -> PolicyVersion:
    if at.tzinfo is None:
        raise ValueError("incident time must be timezone-aware")
    version = session.scalar(
        select(PolicyVersion).where(
            PolicyVersion.policy_id == policy_id,
            PolicyVersion.effective_from <= at,
            or_(PolicyVersion.effective_until.is_(None), PolicyVersion.effective_until > at),
        )
    )
    if version is None:
        raise NoApplicablePolicyError(f"{policy_id} at {at.isoformat()}")
    return version


def applicable_versions_for_domain(
    session: Session, domain: str, at: datetime
) -> list[PolicyVersion]:
    """Every policy governing a case type, each at the version in force at ``at``."""
    policy_ids = session.scalars(select(Policy.id).filter_by(domain=domain).order_by(Policy.id))
    return [applicable_version(session, policy_id, at) for policy_id in policy_ids]
