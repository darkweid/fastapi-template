from enum import StrEnum


class Permission(StrEnum):
    # Dashboard permissions
    VIEW_DASHBOARD = "view_dashboard"
    EDIT_DASHBOARD = "edit_dashboard"

    # User profile permissions
    VIEW_PROFILE = "view_profile"
    EDIT_PROFILE = "edit_profile"

    # User management permissions
    VIEW_USERS = "view_users"
    CREATE_USER = "create_user"
    EDIT_USER = "edit_user"
    DELETE_USER = "delete_user"

    # Content management permissions
    VIEW_CONTENT = "view_content"
    CREATE_CONTENT = "create_content"
    EDIT_CONTENT = "edit_content"
    DELETE_CONTENT = "delete_content"
    PUBLISH_CONTENT = "publish_content"

    # Payment and billing permissions
    VIEW_INVOICES = "view_invoices"
    CREATE_INVOICE = "create_invoice"
    EDIT_INVOICE = "edit_invoice"
    DELETE_INVOICE = "delete_invoice"
    MANAGE_SUBSCRIPTIONS = "manage_subscriptions"
    VIEW_PAYMENT_METHODS = "view_payment_methods"
    ADD_PAYMENT_METHOD = "add_payment_method"
    REMOVE_PAYMENT_METHOD = "remove_payment_method"

    # Reporting permissions
    VIEW_REPORTS = "view_reports"
    GENERATE_REPORT = "generate_report"
    EXPORT_REPORTS = "export_reports"

    # Settings permissions
    VIEW_SETTINGS = "view_settings"
    EDIT_SETTINGS = "edit_settings"

    # System permissions
    VIEW_LOGS = "view_logs"
    MANAGE_BACKUPS = "manage_backups"
