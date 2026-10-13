from datetime import UTC, datetime

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from onramp.cases.models import SupportCase
from onramp.cases.state import CaseState
from onramp.db.types import new_id
from onramp.identity.models import Tenant, User

T0 = datetime(2026, 8, 21, tzinfo=UTC)


@pytest.fixture
def session(onramp_session: Session) -> Session:
    onramp_session.add_all(
        [
            Tenant(id="acme", name="Acme", payout_approval_limit_cents=50_000),
            Tenant(id="globex", name="Globex", payout_approval_limit_cents=50_000),
        ]
    )
    onramp_session.flush()
    onramp_session.add(
        User(id="usr_g", tenant_id="globex", email="g@x.test", display_name="G", role="operator")
    )
    onramp_session.flush()
    return onramp_session


def _case(**overrides: object) -> SupportCase:
    values: dict[str, object] = {
        "id": new_id("case"),
        "tenant_id": "acme",
        "case_type": "payout",
        "state": CaseState.CREATED.value,
        "subject_type": "submission",
        "subject_id": "s_1",
        "worker_id": "w_1",
        "incident_at": T0,
        "incident_basis": "submission.submitted_at",
        "intake_key": new_id("intake"),
        "reported_description": "I was not paid",
        "opened_by_type": "user",
        "opened_by_id": "usr_1",
    }
    values.update(overrides)
    return SupportCase(**values)


def test_second_open_case_for_same_subject_is_rejected(session: Session) -> None:
    session.add(_case())
    session.flush()
    session.add(_case())

    with pytest.raises(IntegrityError, match="uq_support_case_one_open_per_subject"):
        session.flush()


def test_new_case_allowed_once_previous_is_resolved(session: Session) -> None:
    session.add(_case(state=CaseState.RESOLVED.value))
    session.add(_case())

    session.flush()


def test_intake_key_is_unique_per_tenant(session: Session) -> None:
    session.add(_case(intake_key="k1", subject_id="s_1"))
    session.flush()
    session.add(_case(intake_key="k1", subject_id="s_2"))

    with pytest.raises(IntegrityError, match="uq_support_case_tenant_id_intake_key"):
        session.flush()


def test_subject_type_must_match_case_type(session: Session) -> None:
    session.add(_case(case_type="onboarding", subject_type="submission"))

    with pytest.raises(IntegrityError, match="ck_support_case_subject_matches_case_type"):
        session.flush()


def test_cannot_assign_a_user_from_another_tenant(session: Session) -> None:
    session.add(_case(assignee_id="usr_g"))

    with pytest.raises(IntegrityError, match="fk_support_case_tenant_id_assignee_id_app_user"):
        session.flush()


def test_stale_update_fails(session: Session) -> None:
    case = _case()
    session.add(case)
    session.flush()
    assert case.version == 1

    # Simulate another writer having bumped the version since we loaded the row.
    session.execute(
        update(SupportCase)
        .where(SupportCase.id == case.id)
        .values(version=2)
        .execution_options(synchronize_session=False)
    )
    case.state = CaseState.INITIAL_CHECKS.value

    with pytest.raises(StaleDataError):
        session.flush()
