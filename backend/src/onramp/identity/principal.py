from dataclasses import dataclass

from onramp.identity.roles import Permission, Role, has_permission


class PermissionDeniedError(Exception):
    def __init__(self, permission: Permission) -> None:
        super().__init__(f"missing permission {permission.value}")
        self.permission = permission


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. Built only from a verified credential, never from a body."""

    tenant_id: str
    user_id: str
    role: Role

    def require(self, permission: Permission) -> None:
        if not has_permission(self.role, permission):
            raise PermissionDeniedError(permission)
