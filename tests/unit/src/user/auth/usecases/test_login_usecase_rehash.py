from unittest.mock import AsyncMock, Mock

import pytest

from src.core.cache.memory_cache import InMemoryCache
from src.core.errors.exceptions import InstanceProcessingException
from src.user.auth.schemas import LoginUserModel
import src.user.auth.usecases.login as login_usecase
from src.user.auth.usecases.login import (
    INVALID_CREDENTIALS_MESSAGE,
    INVALID_CREDENTIALS_PASSWORD_HASH,
    LoginUserUseCase,
)
from src.user.cache_keys import user_cache_keys
from src.user.models import User
from src.user.policies import AccountAccessViolation
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession, FakeUnitOfWork
from tests.fakes.redis import InMemoryRedis


class FakeUserRepository:
    def __init__(self, user: User | None) -> None:
        self._user = user
        self.update = AsyncMock(return_value=user)

    async def get_single(
        self, session: FakeAsyncSession, **filters: object
    ) -> User | None:
        return self._user


def build_uow(user: User | None, session: FakeAsyncSession) -> FakeUnitOfWork:
    return FakeUnitOfWork(
        session=session,
        repositories={"users": FakeUserRepository(user)},
    )


@pytest.mark.asyncio
async def test_login_rehashes_password_when_needed(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
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
    cache_key = user_cache_keys.summary(user.id)
    await cache.set(cache_key, {"name": "stale"}, ttl=60)

    use_case = LoginUserUseCase(uow=uow, redis_client=fake_redis, cache=cache)
    result = await use_case.execute(
        LoginUserModel(email="user@example.com", password="plain-pass")
    )

    assert result.access_token == "access"
    assert result.refresh_token == "refresh"
    needs_rehash_mock.assert_called_once_with(user.password_hash)
    verify_mock.assert_awaited_once_with("plain-pass", user.password_hash)
    hash_mock.assert_called_once_with("plain-pass")
    uow.users.update.assert_awaited_once_with(
        uow.session,
        {"password_hash": "new-hash"},
        id=user.id,
    )
    uow.flush.assert_not_awaited()
    uow.commit.assert_awaited_once()
    assert await cache.get(cache_key) is None


@pytest.mark.asyncio
async def test_login_does_not_rehash_when_not_needed(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
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
    cache_key = user_cache_keys.summary(user.id)
    await cache.set(cache_key, {"name": "stale"}, ttl=60)

    use_case = LoginUserUseCase(uow=uow, redis_client=fake_redis, cache=cache)
    result = await use_case.execute(
        LoginUserModel(email="user@example.com", password="plain-pass")
    )

    assert result.access_token == "access"
    assert result.refresh_token == "refresh"
    needs_rehash_mock.assert_called_once_with(user.password_hash)
    verify_mock.assert_awaited_once_with("plain-pass", user.password_hash)
    uow.users.update.assert_not_awaited()
    uow.flush.assert_not_awaited()
    uow.commit.assert_awaited_once()
    # Login invalidates unconditionally, even on the no-rehash path where the row
    # is not written - see LoginUserUseCase's docstring for why.
    assert await cache.get(cache_key) is None


@pytest.mark.asyncio
async def test_login_returns_unified_error_for_missing_user_and_uses_dummy_hash(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    uow = build_uow(None, fake_session)
    verify_mock = AsyncMock(return_value=False)
    debug_mock = Mock()
    monkeypatch.setattr(login_usecase, "verify_password", verify_mock)
    monkeypatch.setattr(login_usecase.logger, "debug", debug_mock)

    use_case = LoginUserUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    with pytest.raises(InstanceProcessingException, match=INVALID_CREDENTIALS_MESSAGE):
        await use_case.execute(
            LoginUserModel(email="missing@example.com", password="plain-pass")
        )

    verify_mock.assert_awaited_once_with(
        "plain-pass", INVALID_CREDENTIALS_PASSWORD_HASH
    )
    uow.users.update.assert_not_awaited()
    uow.flush.assert_not_awaited()
    uow.commit.assert_not_awaited()
    debug_mock.assert_called_once()
    assert "not found" in debug_mock.call_args.args[0]


@pytest.mark.asyncio
async def test_login_returns_unified_error_for_wrong_password(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    user = build_user()
    uow = build_uow(user, fake_session)
    verify_mock = AsyncMock(return_value=False)
    debug_mock = Mock()
    monkeypatch.setattr(login_usecase, "verify_password", verify_mock)
    monkeypatch.setattr(login_usecase.logger, "debug", debug_mock)

    use_case = LoginUserUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    with pytest.raises(InstanceProcessingException, match=INVALID_CREDENTIALS_MESSAGE):
        await use_case.execute(
            LoginUserModel(email="user@example.com", password="wrong-pass")
        )

    verify_mock.assert_awaited_once_with("wrong-pass", user.password_hash)
    uow.users.update.assert_not_awaited()
    uow.flush.assert_not_awaited()
    uow.commit.assert_not_awaited()
    debug_mock.assert_called_once()
    assert "Incorrect password" in debug_mock.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user", "expected_violation"),
    [
        (build_user(is_verified=False), AccountAccessViolation.NOT_VERIFIED),
        (build_user(is_verified=True, is_active=False), AccountAccessViolation.BLOCKED),
    ],
)
async def test_login_returns_unified_error_for_account_state_failures(
    user: User,
    expected_violation: AccountAccessViolation,
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    uow = build_uow(user, fake_session)
    verify_mock = AsyncMock(return_value=True)
    debug_mock = Mock()
    monkeypatch.setattr(login_usecase, "verify_password", verify_mock)
    monkeypatch.setattr(login_usecase.logger, "debug", debug_mock)

    use_case = LoginUserUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    with pytest.raises(InstanceProcessingException, match=INVALID_CREDENTIALS_MESSAGE):
        await use_case.execute(
            LoginUserModel(email="user@example.com", password="plain-pass")
        )

    verify_mock.assert_awaited_once_with("plain-pass", user.password_hash)
    uow.users.update.assert_not_awaited()
    uow.flush.assert_not_awaited()
    uow.commit.assert_not_awaited()
    debug_mock.assert_called_once_with(
        "[LoginUser] Account of '%s' fails admission (%s).",
        "us***@ex***",
        expected_violation,
    )
