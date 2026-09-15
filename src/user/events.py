from src.event_log.enums import ObjectType
from src.event_log.events import DomainEvent


class UserSignedIn(DomainEvent):
    code = "user.signed_in"
    object_type = ObjectType.USER


class UserSignInFailed(DomainEvent):
    """No `object_type`: a failed sign-in may name no existing account at all.

    The address is stored unmasked, unlike in application logs. This row is the
    answer to "who tried to get into this account", it is readable only with
    `Permission.VIEW_LOGS`, and a masked address cannot be matched against the
    account it targeted.
    """

    code = "user.sign_in_failed"

    email: str
    reason: str


class UserPasswordChanged(DomainEvent):
    code = "user.password_changed"
    object_type = ObjectType.USER
