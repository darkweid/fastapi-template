from src.user.auth.permissions.enum import Permission
from src.user.enums import UserRole


def has_permission(role: UserRole, permission: Permission) -> bool:
    """Single RBAC lookup for every guard: the role grants the permission or not."""
    return permission in ROLE_PERMISSIONS.get(role, set())


ROLE_PERMISSIONS: dict[UserRole, set[Permission]] = {
    UserRole.ADMIN: {
        Permission.VIEW_DASHBOARD,
        Permission.EDIT_DASHBOARD,
        Permission.VIEW_PROFILE,
        Permission.EDIT_PROFILE,
        Permission.VIEW_USERS,
        Permission.CREATE_USER,
        Permission.EDIT_USER,
        Permission.DELETE_USER,
        Permission.VIEW_CONTENT,
        Permission.CREATE_CONTENT,
        Permission.EDIT_CONTENT,
        Permission.DELETE_CONTENT,
        Permission.PUBLISH_CONTENT,
        Permission.VIEW_INVOICES,
        Permission.CREATE_INVOICE,
        Permission.EDIT_INVOICE,
        Permission.DELETE_INVOICE,
        Permission.MANAGE_SUBSCRIPTIONS,
        Permission.VIEW_PAYMENT_METHODS,
        Permission.ADD_PAYMENT_METHOD,
        Permission.REMOVE_PAYMENT_METHOD,
        Permission.VIEW_REPORTS,
        Permission.GENERATE_REPORT,
        Permission.EXPORT_REPORTS,
        Permission.VIEW_SETTINGS,
        Permission.EDIT_SETTINGS,
        Permission.VIEW_LOGS,
        Permission.MANAGE_BACKUPS,
        Permission.VIEW_NOTES,
        Permission.MANAGE_NOTES,
    },
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
