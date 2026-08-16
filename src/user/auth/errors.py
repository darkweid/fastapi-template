from src.core.errors.codes import ErrorCode
from src.core.errors.exceptions import (
    AccessForbiddenException,
    InstanceProcessingException,
    UnauthorizedException,
)


class UserBlockedError(AccessForbiddenException):
    error_code = ErrorCode.USER_BLOCKED


class UserNotVerifiedError(AccessForbiddenException):
    error_code = ErrorCode.USER_NOT_VERIFIED


class PermissionDeniedError(AccessForbiddenException):
    error_code = ErrorCode.PERMISSION_DENIED


class CsrfFailedError(AccessForbiddenException):
    error_code = ErrorCode.CSRF_FAILED


class TokenExpiredError(UnauthorizedException):
    error_code = ErrorCode.TOKEN_EXPIRED


class InvalidCredentialsError(InstanceProcessingException):
    """Login failures answer HTTP 400 rather than 401 on purpose: SPA
    interceptors commonly treat 401 as "refresh the token and retry", which
    would loop on a login form. The 400 + `invalid_credentials` pair is
    unambiguous for a client."""

    error_code = ErrorCode.INVALID_CREDENTIALS
    log_level = "debug"
