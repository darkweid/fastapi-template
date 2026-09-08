from src.core.errors.codes import ErrorCode
from src.core.errors.exceptions import AccessForbiddenException


class UserBlockedError(AccessForbiddenException):
    error_code = ErrorCode.USER_BLOCKED


class UserNotVerifiedError(AccessForbiddenException):
    error_code = ErrorCode.USER_NOT_VERIFIED
