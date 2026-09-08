from src.user.auth.permissions.enum import Permission
from src.user.enums import UserRole


def has_permission(role: UserRole, permission: Permission) -> bool:
    """Single RBAC lookup for every guard: the role grants the permission or not."""
    return permission in ROLE_PERMISSIONS.get(role, set())


ROLE_PERMISSIONS: dict[UserRole, set[Permission]] = {
    # Admin holds every permission by definition, including any added later:
    # a hand-kept copy of the enum would silently fall behind the next member.
    UserRole.ADMIN: set(Permission),
    UserRole.EDITOR: {
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_PROFILE,
        Permission.EDIT_PROFILE,
        Permission.VIEW_USERS,
        Permission.VIEW_CONTENT,
        Permission.CREATE_CONTENT,
        Permission.EDIT_CONTENT,
        Permission.PUBLISH_CONTENT,
        Permission.VIEW_INVOICES,
        Permission.VIEW_PAYMENT_METHODS,
        Permission.VIEW_REPORTS,
        Permission.GENERATE_REPORT,
        Permission.VIEW_SETTINGS,
    },
    UserRole.VIEWER: {
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_PROFILE,
        Permission.VIEW_CONTENT,
        Permission.VIEW_INVOICES,
        Permission.VIEW_PAYMENT_METHODS,
        Permission.VIEW_REPORTS,
    },
}
