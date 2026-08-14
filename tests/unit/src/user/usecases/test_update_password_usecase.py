from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.cache.memory_cache import InMemoryCache
from src.core.errors.exceptions import (
    AccessForbiddenException,
    InstanceNotFoundException,
    InstanceProcessingException,
)
from src.core.schemas import SuccessResponse
from src.user.auth.schemas import UserNewPassword
from src.user.cache_keys import user_cache_keys
from src.user.models import User
from src.user.usecases.update_password import UpdateUserPasswordUseCase
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession, FakeUnitOfWork
from tests.fakes.redis import InMemoryRedis

CURRENT_PASSWORD = "CurrentPass1!"
NEW_PASSWORD = "StrongPass1!"


class FakeUsersRepository:
    def __init__(self, user: User | None, updated_user: User | None = None):
        self.get_single = AsyncMock(return_value=user)
        self.update = AsyncMock(return_value=updated_user if user else None)


def build_uow(
    session: FakeAsyncSession, users_repo: FakeUsersRepository
) -> FakeUnitOfWork:
    return FakeUnitOfWork(session=session, repositories={"users": users_repo})


def change_password_data(current: str = CURRENT_PASSWORD) -> UserNewPassword:
    return UserNewPassword(current_password=current, password=NEW_PASSWORD)


@pytest.mark.asyncio
async def test_update_password_user_not_found(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    users_repo = FakeUsersRepository(user=None)
    uow = build_uow(fake_session, users_repo)
    use_case = UpdateUserPasswordUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    with pytest.raises(InstanceNotFoundException):
        await use_case.execute(data=change_password_data(), user_id=build_user().id)

    users_repo.update.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_password_rejects_wrong_current_password(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
    cache: InMemoryCache,
) -> None:
    """
    A valid access token alone must not be enough to take over the account: the
    change also wipes every session, so an attacker would lock the owner out.
    """
    user = build_user(password=CURRENT_PASSWORD)
    users_repo = FakeUsersRepository(user=user, updated_user=user)
    uow = build_uow(fake_session, users_repo)
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(
        "src.user.usecases.update_password.invalidate_all_user_sessions",
        invalidate_mock,
    )
    original_hash = user.password_hash

    use_case = UpdateUserPasswordUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    with pytest.raises(AccessForbiddenException):
        await use_case.execute(
            data=change_password_data(current="WrongPass1!"),
            user_id=user.id,
        )

    assert user.password_hash == original_hash
    users_repo.update.assert_not_awaited()
    invalidate_mock.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_password_rejects_reusing_the_current_password(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
    cache: InMemoryCache,
) -> None:
    """
    Going through with it would still sign every session out, so a mistyped form
    would look exactly like a hijack to the user.
    """
    user = build_user(password=CURRENT_PASSWORD)
    users_repo = FakeUsersRepository(user=user, updated_user=user)
    uow = build_uow(fake_session, users_repo)
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(
        "src.user.usecases.update_password.invalidate_all_user_sessions",
        invalidate_mock,
    )

    use_case = UpdateUserPasswordUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    with pytest.raises(InstanceProcessingException):
        await use_case.execute(
            data=UserNewPassword(
                current_password=CURRENT_PASSWORD, password=CURRENT_PASSWORD
            ),
            user_id=user.id,
        )

    users_repo.update.assert_not_awaited()
    invalidate_mock.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_password_missing_row_on_update_is_reported(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
    cache: InMemoryCache,
) -> None:
    """The row can vanish between the read and the write; that is a 404, not a 500."""
    user = build_user(password=CURRENT_PASSWORD)
    users_repo = FakeUsersRepository(user=user, updated_user=None)
    uow = build_uow(fake_session, users_repo)
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(
        "src.user.usecases.update_password.invalidate_all_user_sessions",
        invalidate_mock,
    )

    use_case = UpdateUserPasswordUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    with pytest.raises(InstanceNotFoundException):
        await use_case.execute(data=change_password_data(), user_id=user.id)

    invalidate_mock.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_password_success(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
    cache: InMemoryCache,
) -> None:
    user = build_user(password=CURRENT_PASSWORD)
    users_repo = FakeUsersRepository(user=user, updated_user=user)
    uow = build_uow(fake_session, users_repo)
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(
        "src.user.usecases.update_password.invalidate_all_user_sessions",
        invalidate_mock,
    )
    cache_key = user_cache_keys.summary(user.id)
    await cache.set(cache_key, {"name": "stale"}, ttl=60)

    use_case = UpdateUserPasswordUseCase(uow=uow, redis_client=fake_redis, cache=cache)
    result = await use_case.execute(data=change_password_data(), user_id=user.id)

    assert result == SuccessResponse(success=True)
    uow.commit.assert_awaited_once()
    uow.flush.assert_awaited_once()
    invalidate_mock.assert_awaited_once_with(str(user.id), fake_redis)
    assert await cache.get(cache_key) is None


@pytest.mark.asyncio
async def test_update_password_redis_failure_skips_commit(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
    cache: InMemoryCache,
) -> None:
    user = build_user(password=CURRENT_PASSWORD)
    users_repo = FakeUsersRepository(user=user, updated_user=user)
    uow = build_uow(fake_session, users_repo)
    invalidate_mock = AsyncMock(side_effect=RuntimeError("redis down"))
    monkeypatch.setattr(
        "src.user.usecases.update_password.invalidate_all_user_sessions",
        invalidate_mock,
    )

    use_case = UpdateUserPasswordUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    with pytest.raises(RuntimeError, match="redis down"):
        await use_case.execute(data=change_password_data(), user_id=user.id)

    uow.flush.assert_awaited_once()
    uow.commit.assert_not_awaited()
    uow.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_password_commit_failure_after_invalidation(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
    cache: InMemoryCache,
) -> None:
    user = build_user(password=CURRENT_PASSWORD)
    users_repo = FakeUsersRepository(user=user, updated_user=user)
    uow = build_uow(fake_session, users_repo)
    uow.commit = AsyncMock(side_effect=RuntimeError("db down"))
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(
        "src.user.usecases.update_password.invalidate_all_user_sessions",
        invalidate_mock,
    )

    use_case = UpdateUserPasswordUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    with pytest.raises(RuntimeError, match="db down"):
        await use_case.execute(data=change_password_data(), user_id=user.id)

    invalidate_mock.assert_awaited_once_with(str(user.id), fake_redis)
    uow.flush.assert_awaited_once()
    uow.rollback.assert_awaited_once()
