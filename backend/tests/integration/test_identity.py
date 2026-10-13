import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from onramp.db.types import new_id
from onramp.identity.models import Tenant, User


def _tenant(session: Session) -> Tenant:
    tenant = Tenant(id=new_id("ten"), name="Acme Tasks", payout_approval_limit_cents=50_000)
    session.add(tenant)
    session.flush()
    return tenant


def _user(tenant: Tenant, email: str, role: str = "operator") -> User:
    return User(id=new_id("usr"), tenant_id=tenant.id, email=email, display_name="Op", role=role)


def test_unknown_role_is_rejected_by_database(onramp_session: Session) -> None:
    tenant = _tenant(onramp_session)
    onramp_session.add(_user(tenant, "a@acme.test", role="superuser"))

    with pytest.raises(IntegrityError, match="ck_app_user_known_role"):
        onramp_session.flush()


def test_email_is_unique_within_a_tenant(onramp_session: Session) -> None:
    tenant = _tenant(onramp_session)
    onramp_session.add(_user(tenant, "a@acme.test"))
    onramp_session.flush()
    onramp_session.add(_user(tenant, "a@acme.test"))

    with pytest.raises(IntegrityError, match="uq_app_user_tenant_id_email"):
        onramp_session.flush()


def test_same_email_allowed_in_another_tenant(onramp_session: Session) -> None:
    onramp_session.add(_user(_tenant(onramp_session), "a@acme.test"))
    onramp_session.add(_user(_tenant(onramp_session), "a@acme.test"))

    onramp_session.flush()


def test_payout_limit_must_be_positive(onramp_session: Session) -> None:
    onramp_session.add(Tenant(id=new_id("ten"), name="Bad", payout_approval_limit_cents=0))

    with pytest.raises(IntegrityError, match="ck_tenant_positive_payout_limit"):
        onramp_session.flush()
