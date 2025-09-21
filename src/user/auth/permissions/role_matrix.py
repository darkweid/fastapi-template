from src.user.auth.permissions.enum import Permission
from src.user.enums import UserRole

ROLE_PERMISSIONS: dict[UserRole, set[Permission]] = {
    # Admin - full system access for user-related functions
    UserRole.ADMIN: {
        # Dashboard permissions
        Permission.VIEW_DASHBOARD,
        Permission.EDIT_DASHBOARD,
        # User profile permissions
        Permission.VIEW_PROFILE,
        Permission.EDIT_PROFILE,
        # User management permissions
        Permission.VIEW_USERS,
        Permission.CREATE_USER,
        Permission.EDIT_USER,
        Permission.DELETE_USER,
        # Content management permissions
        Permission.VIEW_CONTENT,
        Permission.CREATE_CONTENT,
        Permission.EDIT_CONTENT,
        Permission.DELETE_CONTENT,
        Permission.PUBLISH_CONTENT,
        # Payment and billing permissions
        Permission.VIEW_INVOICES,
        Permission.CREATE_INVOICE,
        Permission.EDIT_INVOICE,
        Permission.DELETE_INVOICE,
        Permission.MANAGE_SUBSCRIPTIONS,
        Permission.VIEW_PAYMENT_METHODS,
        Permission.ADD_PAYMENT_METHOD,
        Permission.REMOVE_PAYMENT_METHOD,
        # Reporting permissions
        Permission.VIEW_REPORTS,
        Permission.GENERATE_REPORT,
        Permission.EXPORT_REPORTS,
        # Settings permissions
        Permission.VIEW_SETTINGS,
        Permission.EDIT_SETTINGS,
        # System permissions
        Permission.VIEW_LOGS,
        Permission.MANAGE_BACKUPS,
    },
    # Editor - can create and edit content but cannot manage users or system settings
    UserRole.EDITOR: {
        # Dashboard permissions
        Permission.VIEW_DASHBOARD,
        # User profile permissions
        Permission.VIEW_PROFILE,
        Permission.EDIT_PROFILE,
        # User management permissions - limited
        Permission.VIEW_USERS,
        # Content management permissions
        Permission.VIEW_CONTENT,
        Permission.CREATE_CONTENT,
        Permission.EDIT_CONTENT,
        Permission.PUBLISH_CONTENT,
        # Payment and billing permissions - limited
        Permission.VIEW_INVOICES,
        Permission.VIEW_PAYMENT_METHODS,
        # Reporting permissions - limited
        Permission.VIEW_REPORTS,
        Permission.GENERATE_REPORT,
        # Settings permissions - view only
        Permission.VIEW_SETTINGS,
    },
    # Viewer - read-only access to authorized resources
    UserRole.VIEWER: {
        # View only
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_PROFILE,
        Permission.VIEW_CONTENT,
        Permission.VIEW_INVOICES,
        Permission.VIEW_PAYMENT_METHODS,
        Permission.VIEW_REPORTS,
    },
}
