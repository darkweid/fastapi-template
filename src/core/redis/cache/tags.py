from enum import Enum


class CacheTags(str, Enum):
    """
    Enum for cache tags used to categorize and manage cached data.
    """

    VEHICLES = "vehicles"
    VEHICLE_MAKES = "vehicle_makes"
    VEHICLE_MODELS = "vehicle_models"

    DRIVERS = "drivers"
    PROFILES = "profiles"
    MINIMAL = "minimal"

    USER = "user"
    ADMIN = "admin"
    DRIVER = "driver"
    NOTIFICATIONS = "notifications"
