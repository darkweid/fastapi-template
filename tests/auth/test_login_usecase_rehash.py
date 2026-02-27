from unittest.mock import AsyncMock, Mock

import pytest

from src.user.auth.schemas import LoginUserModel
import src.user.auth.usecases.login as login_usecase
from src.user.auth.usecases.login import LoginUserUseCase
from src.user.models import User
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession, FakeUnitOfWork
from tests.fakes.redis import InMemoryRedis


class FakeUserRepository:
    def __init__(self, user: User) -> None:
        self._user = user
        self.update = AsyncMock(return_value=user)

    async def get_single(self, session: FakeAsyncSession, **filters: object) -> User:
        return self._user


def build_uow(user: User, session: FakeAsyncSession) -> FakeUnitOfWork:
    return FakeUnitOfWork(
        session=session,
        repositories={"users": FakeUserRepository(user)},
    )


@pytest.mark.asyncio
async def test_login_rehashes_password_when_needed(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
) -> None:
    user = build_user()
    uow = build_uow(user, fake_session)

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

    use_case = LoginUserUseCase(uow=uow, redis_client=fake_redis)
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
    uow.flush.assert_awaited_once()
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_login_does_not_rehash_when_not_needed(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
) -> None:
    user = build_user()
    uow = build_uow(user, fake_session)

    needs_rehash_mock = Mock(return_value=False)
    verify_mock = AsyncMock(return_value=True)
    access_mock = AsyncMock(return_value="access")
    refresh_mock = AsyncMock(return_value="refresh")

    monkeypatch.setattr(login_usecase, "needs_password_rehash", needs_rehash_mock)
    monkeypatch.setattr(login_usecase, "verify_password", verify_mock)
    monkeypatch.setattr(login_usecase, "create_access_token", access_mock)
    monkeypatch.setattr(login_usecase, "create_refresh_token", refresh_mock)

    use_case = LoginUserUseCase(uow=uow, redis_client=fake_redis)
    result = await use_case.execute(
        LoginUserModel(email="user@example.com", password="plain-pass")
    )

    assert result.access_token == "access"
    assert result.refresh_token == "refresh"
    needs_rehash_mock.assert_called_once_with(user.password_hash)
    uow.users.update.assert_not_awaited()
    uow.flush.assert_awaited_once()
    uow.commit.assert_awaited_once()
