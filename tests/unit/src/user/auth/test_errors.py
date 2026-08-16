import pytest

from src.core.errors.codes import ErrorCode
from src.user.auth import errors


@pytest.mark.parametrize(
    ("error_class", "status", "code"),
    [
        (errors.UserBlockedError, 403, ErrorCode.USER_BLOCKED),
        (errors.UserNotVerifiedError, 403, ErrorCode.USER_NOT_VERIFIED),
        (errors.PermissionDeniedError, 403, ErrorCode.PERMISSION_DENIED),
        (errors.CsrfFailedError, 403, ErrorCode.CSRF_FAILED),
        (errors.TokenExpiredError, 401, ErrorCode.TOKEN_EXPIRED),
        (errors.InvalidCredentialsError, 400, ErrorCode.INVALID_CREDENTIALS),
    ],
)
def test_domain_error_declares_status_and_code(error_class, status, code) -> None:
    assert error_class.status_code == status
    assert error_class.error_code is code


def test_invalid_credentials_logs_at_debug() -> None:
    assert errors.InvalidCredentialsError.log_level == "debug"
