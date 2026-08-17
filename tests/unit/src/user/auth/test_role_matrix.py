from src.user.auth.permissions.enum import Permission
from src.user.auth.permissions.role_matrix import ROLE_PERMISSIONS, has_permission
from src.user.enums import UserRole


def test_has_permission_true_for_granted_permission() -> None:
    assert has_permission(UserRole.ADMIN, Permission.MANAGE_NOTES) is True


def test_has_permission_false_for_missing_permission() -> None:
    assert has_permission(UserRole.VIEWER, Permission.MANAGE_NOTES) is False


def test_has_permission_false_for_role_without_grants() -> None:
    unknown_role = "ghost-role"
    assert unknown_role not in ROLE_PERMISSIONS
    assert has_permission(unknown_role, Permission.VIEW_NOTES) is False  # type: ignore[arg-type]


def test_has_permission_matches_role_matrix_for_every_pair() -> None:
    for role, granted in ROLE_PERMISSIONS.items():
        for permission in Permission:
            assert has_permission(role, permission) is (permission in granted)
