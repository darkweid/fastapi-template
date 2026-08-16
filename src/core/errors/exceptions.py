from typing import Any, ClassVar

from src.core.errors.codes import ErrorCode


class CoreException(Exception):
    """Base project exception.

    Subclasses declare response behaviour as class attributes; a single generic
    handler serializes any of them, so adding an error type means adding a
    subclass, not a handler.
    """

    status_code: ClassVar[int] = 400
    error_code: ClassVar[ErrorCode] = ErrorCode.PROCESSING_ERROR
    log_level: ClassVar[str] = "info"
    capture_to_sentry: ClassVar[bool] = False

    def __init__(
        self, message: str | None = None, additional_info: dict[str, Any] | None = None
    ):
        self.message = message
        self.additional_info = additional_info

    def response_headers(self) -> dict[str, str]:
        return {}

    def body_extras(self) -> dict[str, Any]:
        return {}


class InfrastructureException(CoreException):
    status_code = 500
    error_code = ErrorCode.INFRASTRUCTURE_ERROR
    log_level = "error"
    capture_to_sentry = True


class ServiceUnavailableException(CoreException):
    """A dependency the caller needs right now is unreachable (HTTP 503).

    Deliberately never captured to Sentry: readiness probes poll every few
    seconds, so a minute of downtime would file dozens of identical events for
    a state the orchestrator already sees through the 503.
    """

    status_code = 503
    error_code = ErrorCode.SERVICE_UNAVAILABLE
    log_level = "error"


class InstanceNotFoundException(CoreException):
    status_code = 404
    error_code = ErrorCode.NOT_FOUND


class InstanceProcessingException(CoreException):
    pass


class PayloadTooLargeException(CoreException):
    status_code = 413
    error_code = ErrorCode.PAYLOAD_TOO_LARGE


class FilteringError(CoreException):
    error_code = ErrorCode.INVALID_QUERY
    log_level = "warning"


class UnauthorizedException(CoreException):
    status_code = 401
    error_code = ErrorCode.UNAUTHORIZED
    log_level = "warning"

    def __init__(
        self,
        message: str | None = None,
        www_authenticate: str | None = None,
        additional_info: dict[str, Any] | None = None,
    ):
        super().__init__(message, additional_info)
        # Set it only for schemes the client is expected to answer, such as the
        # Basic challenge that makes a browser show a login prompt for the docs.
        self.www_authenticate = www_authenticate

    def response_headers(self) -> dict[str, str]:
        if self.www_authenticate:
            return {"WWW-Authenticate": self.www_authenticate}
        return {}


class AccessForbiddenException(CoreException):
    status_code = 403
    error_code = ErrorCode.FORBIDDEN
    log_level = "warning"


class TooManyRequestsException(CoreException):
    status_code = 429
    error_code = ErrorCode.RATE_LIMITED

    def __init__(
        self,
        message: str | None = None,
        retry_after: int | None = None,
        additional_info: dict[str, Any] | None = None,
    ):
        super().__init__(message, additional_info)
        self.retry_after = retry_after

    def response_headers(self) -> dict[str, str]:
        if self.retry_after:
            return {"Retry-After": str(self.retry_after)}
        return {}

    def body_extras(self) -> dict[str, Any]:
        if self.retry_after:
            return {"retry_after": self.retry_after}
        return {}
