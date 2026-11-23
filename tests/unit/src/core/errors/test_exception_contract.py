import pytest

from src.core.auth.errors import RefreshTokenReuseError
from src.core.errors import exceptions as exc
from src.core.errors.codes import ErrorCode


@pytest.mark.parametrize(
    ("exception_class", "status", "code"),
    [
        (exc.CoreException, 400, ErrorCode.PROCESSING_ERROR),
        (exc.InfrastructureException, 500, ErrorCode.INFRASTRUCTURE_ERROR),
        (exc.ServiceUnavailableException, 503, ErrorCode.SERVICE_UNAVAILABLE),
        (exc.InstanceNotFoundException, 404, ErrorCode.NOT_FOUND),
        (exc.InstanceProcessingException, 400, ErrorCode.PROCESSING_ERROR),
        (exc.PayloadTooLargeException, 413, ErrorCode.PAYLOAD_TOO_LARGE),
        (exc.FilteringError, 400, ErrorCode.INVALID_QUERY),
        (exc.UnauthorizedException, 401, ErrorCode.UNAUTHORIZED),
        (exc.AccessForbiddenException, 403, ErrorCode.FORBIDDEN),
        (exc.TooManyRequestsException, 429, ErrorCode.RATE_LIMITED),
    ],
)
def test_exception_declares_status_and_code(
    exception_class: type[exc.CoreException], status: int, code: ErrorCode
) -> None:
    assert exception_class.status_code == status
    assert exception_class.error_code is code


def test_default_headers_and_extras_are_empty() -> None:
    error = exc.InstanceNotFoundException("missing")
    assert error.response_headers() == {}
    assert error.body_extras() == {}


def test_unauthorized_carries_www_authenticate_header() -> None:
    error = exc.UnauthorizedException("nope", www_authenticate='Basic realm="docs"')
    assert error.response_headers() == {"WWW-Authenticate": 'Basic realm="docs"'}


def test_too_many_requests_carries_retry_after() -> None:
    error = exc.TooManyRequestsException("slow down", retry_after=42)
    assert error.response_headers() == {"Retry-After": "42"}
    assert error.body_extras() == {"retry_after": 42}


def test_sentry_capture_flag() -> None:
    assert exc.InfrastructureException.capture_to_sentry is True
    assert exc.ServiceUnavailableException.capture_to_sentry is False
    assert exc.CoreException.capture_to_sentry is False


@pytest.mark.parametrize(
    ("exception_class", "level"),
    [
        (exc.FilteringError, "debug"),
        (exc.UnauthorizedException, "debug"),
        (exc.AccessForbiddenException, "info"),
        (exc.ServiceUnavailableException, "error"),
        (RefreshTokenReuseError, "warning"),
    ],
)
def test_a_user_mistake_is_not_logged_as_a_fault(
    exception_class: type[exc.CoreException], level: str
) -> None:
    """A bad query or an expired token is the caller's doing; a replayed refresh
    token is a security event and stays a warning."""
    assert exception_class.log_level == level
