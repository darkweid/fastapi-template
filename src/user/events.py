from src.event_log.enums import ObjectType
from src.event_log.events import DomainEvent


class UserRegistered(DomainEvent):
    code = "user.registered"
    object_type = ObjectType.USER


class UserEmailVerified(DomainEvent):
    code = "user.email_verified"
    object_type = ObjectType.USER


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


class UserSignedOut(DomainEvent):
    code = "user.signed_out"
    object_type = ObjectType.USER

    all_sessions: bool


class UserPasswordChanged(DomainEvent):
    code = "user.password_changed"
    object_type = ObjectType.USER


class UserPasswordResetRequested(DomainEvent):
    """Recorded against the account the reset was asked for, by an actor that
    has proved nothing yet - anyone may type an address into that form. The
    pair of this row and `user.password_reset` is what tells a takeover attempt
    from an owner who forgot their password."""

    code = "user.password_reset_requested"
    object_type = ObjectType.USER


class UserPasswordReset(DomainEvent):
    code = "user.password_reset"
    object_type = ObjectType.USER


class UserProfileUpdated(DomainEvent):
    code = "user.profile_updated"
    object_type = ObjectType.USER

    fields: list[str]
