from enum import Enum


class CacheTags(str, Enum):
    """
    Enum for cache tags used to categorize and manage cached data.
    """

    USER = "user"
    ADMIN = "admin"
    NOTIFICATIONS = "notifications"
