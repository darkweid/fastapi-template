from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core.cache.interface import CacheKey
from src.core.cache.memory_cache import InMemoryCache
from src.core.errors.exceptions import InstanceNotFoundException
from src.user.schemas import UserProfileUpdateModel
from src.user.usecases.update_profile import UpdateUserProfileUseCase
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession, FakeUnitOfWork


class FakeUsersRepository:
    def __init__(self, updated_user):
        self.update = AsyncMock(return_value=updated_user)


def build_uow(
    session: FakeAsyncSession, users_repo: FakeUsersRepository
) -> FakeUnitOfWork:
    return FakeUnitOfWork(session=session, repositories={"users": users_repo})


@pytest.mark.asyncio
async def test_profile_update_bumps_cache_namespace(
    fake_session: FakeAsyncSession, cache: InMemoryCache
) -> None:
    user = build_user()
    users_repo = FakeUsersRepository(updated_user=user)
    uow = build_uow(fake_session, users_repo)
    key = CacheKey(namespace=f"user:{user.id}", suffix="summary")
    await cache.set(key, {"name": "stale"}, ttl=60)
    use_case = UpdateUserProfileUseCase(uow=uow, cache=cache)

    await use_case.execute(
        data=UserProfileUpdateModel(first_name="Grace"), user_id=user.id
    )

    assert await cache.get(key) is None
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_profile_update_bumps_cache_before_and_after_commit(
    fake_session: FakeAsyncSession, cache: InMemoryCache
) -> None:
    # Pre-commit bump covers the crash direction; the post-commit bump closes
    # the race where a reader re-caches stale data between bump and commit.
    user = build_user()
    users_repo = FakeUsersRepository(updated_user=user)
    uow = build_uow(fake_session, users_repo)
    invalidate_spy = AsyncMock(wraps=cache.invalidate)
    cache.invalidate = invalidate_spy  # type: ignore[method-assign]
    use_case = UpdateUserProfileUseCase(uow=uow, cache=cache)

    await use_case.execute(
        data=UserProfileUpdateModel(first_name="Grace"), user_id=user.id
    )

    namespace = f"user:{user.id}"
    assert invalidate_spy.await_count == 2
    for call in invalidate_spy.await_args_list:
        assert call.args == (namespace,)


@pytest.mark.asyncio
async def test_missing_user_raises_not_found(
    fake_session: FakeAsyncSession, cache: InMemoryCache
) -> None:
    users_repo = FakeUsersRepository(updated_user=None)
    uow = build_uow(fake_session, users_repo)
    use_case = UpdateUserProfileUseCase(uow=uow, cache=cache)

    with pytest.raises(InstanceNotFoundException):
        await use_case.execute(
            data=UserProfileUpdateModel(first_name="Grace"), user_id=uuid4()
        )

    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_is_cold_when_commit_fails(
    fake_session: FakeAsyncSession, cache: InMemoryCache
) -> None:
    user = build_user()
    users_repo = FakeUsersRepository(updated_user=user)
    uow = build_uow(fake_session, users_repo)
    uow.commit = AsyncMock(side_effect=RuntimeError("db down"))
    key = CacheKey(namespace=f"user:{user.id}", suffix="summary")
    await cache.set(key, {"name": "stale"}, ttl=60)
    use_case = UpdateUserProfileUseCase(uow=uow, cache=cache)

    with pytest.raises(RuntimeError):
        await use_case.execute(
            data=UserProfileUpdateModel(first_name="Grace"), user_id=user.id
        )

    assert await cache.get(key) is None
    uow.rollback.assert_awaited_once()
