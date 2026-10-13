"""Roles and the permissions they grant (PRODUCT_SPEC section 2).

Permissions are checked server-side on every request. The console may hide controls, but that
is presentation, not enforcement.
"""

from enum import StrEnum


class Role(StrEnum):
    OPERATOR = "operator"
    LEAD = "lead"
    ADMIN = "admin"
    MARKETPLACE_SERVICE = "marketplace_service"


class Permission(StrEnum):
    CASE_READ = "case.read"
    CASE_CREATE = "case.create"
    CASE_INVESTIGATE = "case.investigate"
    CASE_ESCALATE = "case.escalate"
    CASE_WORK_ESCALATED = "case.work_escalated"
    PROPOSAL_DECIDE = "proposal.decide"
    ACTION_PROPOSE = "action.propose"
    APPROVE_MEDIUM_RISK = "approve.medium_risk"
    APPROVE_HIGH_RISK = "approve.high_risk"
    USERS_MANAGE = "users.manage"
    AUDIT_READ = "audit.read"


_OPERATOR = frozenset(
    {
        Permission.CASE_READ,
        Permission.CASE_CREATE,
        Permission.CASE_INVESTIGATE,
        Permission.CASE_ESCALATE,
        Permission.PROPOSAL_DECIDE,
        Permission.ACTION_PROPOSE,
        Permission.APPROVE_MEDIUM_RISK,
        Permission.AUDIT_READ,
    }
)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OPERATOR: _OPERATOR,
    Role.LEAD: _OPERATOR | {Permission.APPROVE_HIGH_RISK, Permission.CASE_WORK_ESCALATED},
    Role.ADMIN: frozenset({Permission.CASE_READ, Permission.USERS_MANAGE, Permission.AUDIT_READ}),
    Role.MARKETPLACE_SERVICE: frozenset({Permission.CASE_CREATE}),
}


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]
