import pytest

from onramp.identity.roles import Permission, Role, has_permission


def test_lead_has_everything_an_operator_has() -> None:
    for permission in Permission:
        if has_permission(Role.OPERATOR, permission):
            assert has_permission(Role.LEAD, permission)


@pytest.mark.parametrize("role", [Role.OPERATOR, Role.ADMIN, Role.MARKETPLACE_SERVICE])
def test_only_leads_approve_high_risk(role: Role) -> None:
    assert not has_permission(role, Permission.APPROVE_HIGH_RISK)


def test_admin_cannot_approve_anything() -> None:
    assert not has_permission(Role.ADMIN, Permission.APPROVE_MEDIUM_RISK)
    assert not has_permission(Role.ADMIN, Permission.APPROVE_HIGH_RISK)


def test_marketplace_service_can_only_create_cases() -> None:
    granted = {p for p in Permission if has_permission(Role.MARKETPLACE_SERVICE, p)}
    assert granted == {Permission.CASE_CREATE}
