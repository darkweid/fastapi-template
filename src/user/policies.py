from enum import StrEnum
from typing import Protocol

from src.user.auth.errors import (
    InvalidCredentialsError,
    UserBlockedError,
    UserNotVerifiedError,
)

INVALID_CREDENTIALS_MESSAGE = "Incorrect email or password."


class AccountLike(Protocol):
    """The two flags the admission rule reads.

    A Protocol instead of the ORM model keeps this module import-free of
    sqlalchemy, so the rule is unit-testable with a plain object.
    """

    is_active: bool
    is_verified: bool


class AccountAccessViolation(StrEnum):
    BLOCKED = "blocked"
    NOT_VERIFIED = "not_verified"


def account_access_violation(user: AccountLike) -> AccountAccessViolation | None:
    """Single home of the "may this account be used" rule.

    Blocked wins over unverified: a blocked account must stay unusable even
    after it verifies its email.
    """
    if not user.is_active:
        return AccountAccessViolation.BLOCKED
    if not user.is_verified:
        return AccountAccessViolation.NOT_VERIFIED
    return None


def ensure_can_authenticate(user: AccountLike) -> None:
    """Login gate: collapse every violation into InvalidCredentialsError.

    A login response must not confirm that an account exists or reveal its
    state (anti-enumeration). Callers already holding a valid token get the
    real reason instead - see ensure_can_use_session.
    """
    if account_access_violation(user) is not None:
        raise InvalidCredentialsError(INVALID_CREDENTIALS_MESSAGE)


def ensure_can_use_session(user: AccountLike) -> None:
    """Gate for callers that already proved the account exists with a valid
    token; masking would only hurt the client, so the reason is reported."""
    violation = account_access_violation(user)
    if violation is AccountAccessViolation.BLOCKED:
        raise UserBlockedError("User is blocked")
    if violation is AccountAccessViolation.NOT_VERIFIED:
        raise UserNotVerifiedError("User is not verified")


def verification_pending(user: AccountLike) -> bool:
    """True while the account still needs email verification (resend flow)."""
    return not user.is_verified
