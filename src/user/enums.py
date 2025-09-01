from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"  # Full system access
    EDITOR = "editor"  # Can edit content but not manage users or system settings
    VIEWER = "viewer"  # Read-only access to authorized resources

    @classmethod
    def values(cls) -> set[str]:
        return {item.value for item in cls.__members__.values()}
