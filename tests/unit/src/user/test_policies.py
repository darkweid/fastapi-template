from dataclasses import dataclass
import inspect

import pytest

from src.user import policies
from src.user.auth.errors import (
    InvalidCredentialsError,
    UserBlockedError,
    UserNotVerifiedError,
)


@dataclass
class StubAccount:
    is_active: bool = True
    is_verified: bool = True


def test_admitted_account_has_no_violation() -> None:
    assert policies.account_access_violation(StubAccount()) is None


def test_blocked_wins_over_unverified() -> None:
    account = StubAccount(is_active=False, is_verified=False)
    violation = policies.account_access_violation(account)
    assert violation is policies.AccountAccessViolation.BLOCKED


def test_login_masks_every_violation() -> None:
    with pytest.raises(InvalidCredentialsError) as excinfo:
        policies.ensure_can_authenticate(StubAccount(is_active=False))
    assert excinfo.value.message == policies.INVALID_CREDENTIALS_MESSAGE
    with pytest.raises(InvalidCredentialsError):
        policies.ensure_can_authenticate(StubAccount(is_verified=False))


def test_session_gate_reports_the_reason() -> None:
    with pytest.raises(UserBlockedError):
        policies.ensure_can_use_session(StubAccount(is_active=False))
    with pytest.raises(UserNotVerifiedError):
        policies.ensure_can_use_session(StubAccount(is_verified=False))


def test_admitted_account_passes_both_gates() -> None:
    policies.ensure_can_authenticate(StubAccount())
    policies.ensure_can_use_session(StubAccount())


def test_verification_pending() -> None:
    assert policies.verification_pending(StubAccount(is_verified=False)) is True
    assert policies.verification_pending(StubAccount()) is False


def test_policies_module_stays_pure() -> None:
    source = inspect.getsource(policies)
    import_lines = [
        line for line in source.splitlines() if line.startswith(("import ", "from "))
    ]
    for line in import_lines:
        assert not any(
            name in line
            for name in ("fastapi", "redis", "sqlalchemy", "src.core.database")
        ), line
