from collections.abc import Callable
from typing import Annotated

from fastapi import Depends

from src.user.auth.dependencies import get_current_user
from src.user.auth.errors import PermissionDeniedError
from src.user.auth.permissions.enum import Permission
from src.user.auth.permissions.role_matrix import has_permission
from src.user.models import User


def require_permission(
    required_permission: Permission,
) -> Callable[[Annotated[User, Depends(get_current_user)]], User]:
    # Account admission (active + verified) is enforced by get_current_user;
    # this checker only decides RBAC.
    def checker(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if not has_permission(current_user.role, required_permission):
            raise PermissionDeniedError("Permission denied")
        return current_user

    return checker
