from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from onramp.policy.applicability import (
    NoApplicablePolicyError,
    applicable_version,
    applicable_versions_for_domain,
)
from onramp.policy.models import Policy, PolicyVersion

JAN = datetime(2026, 1, 1, tzinfo=UTC)
JUL = datetime(2026, 7, 1, tzinfo=UTC)


def _version(version: int, start: datetime, end: datetime | None) -> PolicyVersion:
    return PolicyVersion(
        policy_id="payout",
        version=version,
        effective_from=start,
        effective_until=end,
        parameters={},
        source_sha256="0" * 64,
    )


@pytest.fixture
def session(onramp_session: Session) -> Session:
    onramp_session.add(Policy(id="payout", domain="payout", title="Payout policy"))
    onramp_session.flush()
    onramp_session.add_all([_version(11, JAN, JUL), _version(12, JUL, None)])
    onramp_session.flush()
    return onramp_session


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        (datetime(2026, 3, 15, tzinfo=UTC), 11),
        (datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC), 11),
        (JUL, 12),  # boundary belongs to the newer version: periods are [from, until)
        (datetime(2027, 1, 1, tzinfo=UTC), 12),
    ],
)
def test_version_in_force_at_incident_time(session: Session, at: datetime, expected: int) -> None:
    assert applicable_version(session, "payout", at).version == expected


def test_incident_before_any_version_has_no_policy(session: Session) -> None:
    with pytest.raises(NoApplicablePolicyError):
        applicable_version(session, "payout", datetime(2025, 12, 31, tzinfo=UTC))


def test_naive_incident_time_is_rejected(session: Session) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        applicable_version(session, "payout", datetime(2026, 3, 1))


def test_domain_lookup_returns_version_in_force(session: Session) -> None:
    versions = applicable_versions_for_domain(session, "payout", JAN)

    assert [(v.policy_id, v.version) for v in versions] == [("payout", 11)]


def test_overlapping_periods_are_rejected_by_database(session: Session) -> None:
    session.add(_version(13, datetime(2026, 9, 1, tzinfo=UTC), None))

    with pytest.raises(IntegrityError, match="no_overlapping_periods"):
        session.flush()


def test_empty_period_is_rejected(session: Session) -> None:
    session.add(
        PolicyVersion(
            policy_id="payout",
            version=99,
            effective_from=JUL,
            effective_until=JUL,
            parameters={},
            source_sha256="0" * 64,
        )
    )

    with pytest.raises(IntegrityError, match="non_empty_period"):
        session.flush()
