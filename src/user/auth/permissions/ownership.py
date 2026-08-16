from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request

from src.core.errors.exceptions import InstanceNotFoundException
from src.user.auth.dependencies import get_current_user
from src.user.auth.permissions.enum import Permission
from src.user.auth.permissions.role_matrix import ROLE_PERMISSIONS
from src.user.models import User


def require_self_or_permission(
    path_param: str, fallback: Permission
) -> Callable[..., User]:
    """Object-level authorization: the caller owns the object OR holds a permission.

    The reference BOLA guard for endpoints that take another user's identifier
    in the path. Failure answers 404, never 403: a foreign id must stay
    indistinguishable from a nonexistent one, so the endpoint cannot be used to
    enumerate identifiers.

    For domain entities with an `owner_id` column the same rule is applied in
    the UseCase after loading the object (compare the field, raise
    InstanceNotFoundException); this dependency covers the case where ownership
    is visible right in the path.
    """

    def checker(
        request: Request,
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        object_id = request.path_params[path_param]
        if str(current_user.id) == str(object_id):
            return current_user
        if fallback in ROLE_PERMISSIONS.get(current_user.role, set()):
            return current_user
        raise InstanceNotFoundException("User not found.")

    return checker
