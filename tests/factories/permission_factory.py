from __future__ import annotations

from src.user.auth.permissions.enum import Permission
from src.user.auth.permissions.role_matrix import ROLE_PERMISSIONS
from src.user.enums import UserRole


def permissions_for_role(role: UserRole) -> set[Permission]:
    return set(ROLE_PERMISSIONS.get(role, set()))


def build_permissions(*permissions: Permission) -> set[Permission]:
    return set(permissions)
