from enum import StrEnum


class ActorType(StrEnum):
    """Which identity store the actor lives in, never which role they played.

    A role can change between the action and the day someone reads the row;
    the store the account came from cannot. A project that adds a second realm
    (staff, partner) adds a member here and a constructor on `Actor`.
    """

    USER = "user"
    SYSTEM = "system"
    ANONYMOUS = "anonymous"


class ObjectType(StrEnum):
    """What the event is about. A project extends this as it logs new entities."""

    USER = "user"
    NOTE = "note"
