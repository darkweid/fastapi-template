from __future__ import annotations

from unittest.mock import AsyncMock

from pydantic import ValidationError
import pytest

from scripts.create_admin import _AdminCredentialsModel, ensure_admin, main
from src.core.utils.security import is_password_hash
from src.user.enums import UserRole
from src.user.models import User
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession, FakeUnitOfWork

STRONG_PASSWORD = "StrongPass1!"
WEAK_PASSWORD = "weak"
INVALID_EMAIL = "not-an-email"


class FakeUsersRepository:
    def __init__(self, user: User | None) -> None:
        self.get_single = AsyncMock(return_value=user)
        self.create = AsyncMock(return_value=None)
        self.update = AsyncMock(return_value=user)


def build_uow(
    session: FakeAsyncSession, users_repo: FakeUsersRepository
) -> FakeUnitOfWork:
    return FakeUnitOfWork(session=session, repositories={"users": users_repo})


@pytest.mark.asyncio
async def test_ensure_admin_creates_when_absent(fake_session: FakeAsyncSession) -> None:
    users_repo = FakeUsersRepository(user=None)
    uow = build_uow(fake_session, users_repo)

    await ensure_admin(
        uow,
        email="Admin@Example.com",
        password=STRONG_PASSWORD,
        first_name="Admin",
        last_name="User",
        username="admin",
        phone_number="+10000000000",
    )

    users_repo.get_single.assert_awaited_once_with(
        fake_session, email="admin@example.com"
    )
    users_repo.update.assert_not_awaited()
    users_repo.create.assert_awaited_once()

    call = users_repo.create.await_args
    assert call is not None
    session_arg, data = call.args
    assert session_arg is fake_session
    assert data["email"] == "admin@example.com"
    assert data["username"] == "admin"
    assert data["role"] == UserRole.ADMIN
    assert data["is_active"] is True
    assert data["is_verified"] is True
    assert data["password_hash"] != STRONG_PASSWORD
    assert is_password_hash(data["password_hash"])
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_admin_promotes_existing_user_without_touching_password(
    fake_session: FakeAsyncSession,
) -> None:
    existing = build_user(
        email="admin@example.com",
        role=UserRole.VIEWER,
        is_active=False,
        is_verified=False,
    )
    original_hash = existing.password_hash
    users_repo = FakeUsersRepository(user=existing)
    uow = build_uow(fake_session, users_repo)

    await ensure_admin(
        uow,
        email="admin@example.com",
        password=STRONG_PASSWORD,
        first_name="Admin",
        last_name="User",
        username="admin",
        phone_number="+10000000000",
    )

    users_repo.create.assert_not_awaited()
    users_repo.update.assert_awaited_once_with(
        fake_session,
        {"role": UserRole.ADMIN, "is_active": True, "is_verified": True},
        id=existing.id,
    )
    # The update payload above carries no password_hash key, and the fake
    # repository never mutates the instance, so this also proves the stored
    # hash was never touched.
    assert existing.password_hash == original_hash
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_admin_rejects_weak_password_before_db_access(
    fake_session: FakeAsyncSession,
) -> None:
    users_repo = FakeUsersRepository(user=None)
    uow = build_uow(fake_session, users_repo)

    with pytest.raises(ValidationError):
        await ensure_admin(
            uow,
            email="admin@example.com",
            password=WEAK_PASSWORD,
            first_name="Admin",
            last_name="User",
            username="admin",
            phone_number="+10000000000",
        )

    users_repo.get_single.assert_not_awaited()
    users_repo.create.assert_not_awaited()
    users_repo.update.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_admin_rejects_invalid_email_before_db_access(
    fake_session: FakeAsyncSession,
) -> None:
    """An email EmailStr rejects would create an account nobody could log
    into, since login validates the same way - reject it up front instead."""
    users_repo = FakeUsersRepository(user=None)
    uow = build_uow(fake_session, users_repo)

    with pytest.raises(ValidationError):
        await ensure_admin(
            uow,
            email=INVALID_EMAIL,
            password=STRONG_PASSWORD,
            first_name="Admin",
            last_name="User",
            username="admin",
            phone_number="+10000000000",
        )

    users_repo.get_single.assert_not_awaited()
    users_repo.create.assert_not_awaited()
    users_repo.update.assert_not_awaited()
    uow.commit.assert_not_awaited()


def test_password_validation_message_does_not_echo_password() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _AdminCredentialsModel(email="admin@example.com", password=WEAK_PASSWORD)

    message = exc_info.value.errors()[0]["msg"]
    assert WEAK_PASSWORD not in message


def test_email_validation_message_does_not_echo_password() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _AdminCredentialsModel(email=INVALID_EMAIL, password=WEAK_PASSWORD)

    for error in exc_info.value.errors():
        assert WEAK_PASSWORD not in error["msg"]


@pytest.mark.asyncio
async def test_main_exits_with_usage_when_email_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.setenv("ADMIN_PASSWORD", STRONG_PASSWORD)

    with pytest.raises(SystemExit) as exc_info:
        await main()

    assert exc_info.value.code == 2
    assert "Usage:" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_main_exits_with_usage_when_password_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        await main()

    assert exc_info.value.code == 2
    assert "Usage:" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_main_exits_2_for_invalid_email(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ADMIN_EMAIL", INVALID_EMAIL)
    monkeypatch.setenv("ADMIN_PASSWORD", STRONG_PASSWORD)

    with pytest.raises(SystemExit) as exc_info:
        await main()

    assert exc_info.value.code == 2
    assert "Invalid ADMIN_EMAIL:" in capsys.readouterr().err
