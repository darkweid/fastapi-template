from src.core.errors.codes import ErrorCode
from src.core.errors.exceptions import (
    AccessForbiddenException,
    InstanceProcessingException,
    UnauthorizedException,
)


class PermissionDeniedError(AccessForbiddenException):
    error_code = ErrorCode.PERMISSION_DENIED


class CsrfFailedError(AccessForbiddenException):
    error_code = ErrorCode.CSRF_FAILED


class TokenExpiredError(UnauthorizedException):
    error_code = ErrorCode.TOKEN_EXPIRED


class RefreshTokenReuseError(UnauthorizedException):
    """A rotated-out refresh token was presented again and its session ended.

    Same status and code as any other refused token. The level is pinned: this
    is a security event whatever an ordinary 401 logs at.
    """

    log_level = "warning"

    def __init__(self, message: str, *, subject_id: str, session_id: str) -> None:
        super().__init__(message)
        self.subject_id = subject_id
        self.session_id = session_id


class ChallengeAlreadyLiveError(InstanceProcessingException):
    """A single-use token was not issued: the one a newer request issued for the
    same purpose and identifier is live."""


class InvalidCredentialsError(InstanceProcessingException):
    """Login failures answer HTTP 400 rather than 401 on purpose: SPA
    interceptors commonly treat 401 as "refresh the token and retry", which
    would loop on a login form. The 400 + `invalid_credentials` pair is
    unambiguous for a client."""

    error_code = ErrorCode.INVALID_CREDENTIALS
    log_level = "debug"
