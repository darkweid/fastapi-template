from collections.abc import Callable
from typing import Annotated

from fastapi import Depends

from src.core.errors.exceptions import (
    AccessForbiddenException,
    PermissionDeniedException,
)
from src.user.auth.dependencies import get_current_user
from src.user.auth.permissions.enum import Permission
from src.user.auth.permissions.role_matrix import ROLE_PERMISSIONS
from src.user.models import User


def require_permission(
    required_permission: Permission,
) -> Callable[[Annotated[User, Depends(get_current_user)]], User]:
    def checker(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if not current_user.is_active:
            raise AccessForbiddenException(
                "You do not have permission to access this resource. User is blocked",
            )

        if not current_user.is_verified:
            raise AccessForbiddenException(
                "You do not have permission to access this resource. Verified users only",
            )

        permissions = ROLE_PERMISSIONS.get(current_user.role, set())
        if required_permission not in permissions:
            raise PermissionDeniedException("Permission denied")

        return current_user

    return checker
