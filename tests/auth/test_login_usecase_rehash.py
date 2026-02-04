from dataclasses import dataclass
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest

from src.user.auth.schemas import LoginUserModel
import src.user.auth.usecases.login as login_usecase
from src.user.auth.usecases.login import LoginUserUseCase


@dataclass
class FakeUser:
    id: UUID
    password_hash: str
    is_verified: bool
    is_active: bool


class FakeSession:
    def __init__(self) -> None:
        self.flush = AsyncMock()


class FakeUserRepository:
    def __init__(self, user: FakeUser) -> None:
        self._user = user
        self.update = AsyncMock(return_value=user)

    async def get_single(self, session: FakeSession, **filters: object) -> FakeUser:
        return self._user


class FakeUnitOfWork:
    def __init__(self, user: FakeUser) -> None:
        self.session = FakeSession()
        self.users = FakeUserRepository(user)
        self.commit = AsyncMock()

    async def __aenter__(self) -> "FakeUnitOfWork":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        return None


@pytest.mark.asyncio
async def test_login_rehashes_password_when_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = FakeUser(
        id=uuid4(),
        password_hash="existing-hash",
        is_verified=True,
        is_active=True,
    )
    uow = FakeUnitOfWork(user)
    redis_client = AsyncMock()

    needs_rehash_mock = Mock(return_value=True)
    hash_mock = Mock(return_value="new-hash")
    verify_mock = AsyncMock(return_value=True)
    access_mock = AsyncMock(return_value="access")
    refresh_mock = AsyncMock(return_value="refresh")

    monkeypatch.setattr(login_usecase, "needs_password_rehash", needs_rehash_mock)
    monkeypatch.setattr(login_usecase, "hash_password", hash_mock)
    monkeypatch.setattr(login_usecase, "verify_password", verify_mock)
    monkeypatch.setattr(login_usecase, "create_access_token", access_mock)
    monkeypatch.setattr(login_usecase, "create_refresh_token", refresh_mock)

    use_case = LoginUserUseCase(uow=uow, redis_client=redis_client)
    result = await use_case.execute(
        LoginUserModel(email="user@example.com", password="plain-pass")
    )

    assert result.access_token == "access"
    assert result.refresh_token == "refresh"
    needs_rehash_mock.assert_called_once_with(user.password_hash)
    hash_mock.assert_called_once_with("plain-pass")
    uow.users.update.assert_awaited_once_with(
        uow.session,
        {"password_hash": "new-hash"},
        id=user.id,
    )
    uow.session.flush.assert_awaited_once()
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_login_does_not_rehash_when_not_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = FakeUser(
        id=uuid4(),
        password_hash="existing-hash",
        is_verified=True,
        is_active=True,
    )
    uow = FakeUnitOfWork(user)
    redis_client = AsyncMock()

    needs_rehash_mock = Mock(return_value=False)
    verify_mock = AsyncMock(return_value=True)
    access_mock = AsyncMock(return_value="access")
    refresh_mock = AsyncMock(return_value="refresh")

    monkeypatch.setattr(login_usecase, "needs_password_rehash", needs_rehash_mock)
    monkeypatch.setattr(login_usecase, "verify_password", verify_mock)
    monkeypatch.setattr(login_usecase, "create_access_token", access_mock)
    monkeypatch.setattr(login_usecase, "create_refresh_token", refresh_mock)

    use_case = LoginUserUseCase(uow=uow, redis_client=redis_client)
    result = await use_case.execute(
        LoginUserModel(email="user@example.com", password="plain-pass")
    )

    assert result.access_token == "access"
    assert result.refresh_token == "refresh"
    needs_rehash_mock.assert_called_once_with(user.password_hash)
    uow.users.update.assert_not_awaited()
    uow.session.flush.assert_awaited_once()
    uow.commit.assert_awaited_once()
