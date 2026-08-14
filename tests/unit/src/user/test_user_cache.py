from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx2
import pytest

from src.core.cache.memory_cache import InMemoryCache
from src.core.database.session import get_session, get_unit_of_work
from src.user.auth.dependencies import get_current_user
from src.user.cache_keys import user_cache_keys
from src.user.dependencies import get_user_service
from src.user.enums import UserRole
from src.user.models import User
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession, FakeUnitOfWork
from tests.helpers.limiter import noop_rate_limiter
from tests.helpers.overrides import DependencyOverrides
from tests.helpers.providers import ProvideAsyncValue, ProvideValue


class FakeUserService:
    def __init__(self, user: User) -> None:
        self.get_single_or_404 = AsyncMock(return_value=user)


class FakeUsersRepository:
    def __init__(self, updated_user: User | None) -> None:
        self.update = AsyncMock(return_value=updated_user)


@pytest.fixture(autouse=True)
def disable_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.core.limiter.depends.RateLimiter.__call__",
        noop_rate_limiter,
    )


async def test_summary_key_is_namespaced_per_user() -> None:
    user_id = uuid4()

    key = user_cache_keys.summary(user_id)

    assert key.namespace == f"user:{user_id}"
    assert key.suffix == "summary"


async def test_get_user_by_id_serves_second_request_from_cache(
    async_client: httpx2.AsyncClient,
    dependency_overrides: DependencyOverrides,
    fake_session: FakeAsyncSession,
    cache: InMemoryCache,
) -> None:
    admin_user = build_user(role=UserRole.ADMIN)
    target_user = build_user()
    dependency_overrides.set(get_current_user, ProvideValue(admin_user))
    dependency_overrides.set(
        get_user_service, ProvideValue(FakeUserService(target_user))
    )
    dependency_overrides.set(get_session, ProvideAsyncValue(fake_session))

    first = await async_client.get(f"/v1/users/{target_user.id}")
    second = await async_client.get(f"/v1/users/{target_user.id}")

    assert first.status_code == 200
    assert first.headers["X-Cache-Status"] == "MISS"
    assert second.headers["X-Cache-Status"] == "HIT"
    assert second.json() == first.json()


async def test_profile_update_invalidates_cached_summary(
    async_client: httpx2.AsyncClient,
    dependency_overrides: DependencyOverrides,
    fake_session: FakeAsyncSession,
    cache: InMemoryCache,
) -> None:
    # The GET/PATCH pair below targets the same user on purpose: PATCH /me only
    # ever updates the caller's own row, so proving invalidation requires the
    # cached summary and the mutated profile to belong to the same identity.
    user = build_user(role=UserRole.ADMIN)
    dependency_overrides.set(get_current_user, ProvideValue(user))
    dependency_overrides.set(get_user_service, ProvideValue(FakeUserService(user)))
    dependency_overrides.set(get_session, ProvideAsyncValue(fake_session))

    async def apply_update(
        session: FakeAsyncSession, data: dict[str, Any], **filters: object
    ) -> User:
        for field, value in data.items():
            setattr(user, field, value)
        return user

    users_repo = FakeUsersRepository(updated_user=user)
    users_repo.update = AsyncMock(side_effect=apply_update)
    uow = FakeUnitOfWork(session=fake_session, repositories={"users": users_repo})
    dependency_overrides.set(get_unit_of_work, ProvideAsyncValue(uow))

    await async_client.get(f"/v1/users/{user.id}")

    patch_response = await async_client.patch(
        "/v1/users/me", json={"first_name": "Grace"}
    )
    after = await async_client.get(f"/v1/users/{user.id}")

    assert patch_response.status_code == 200
    assert after.headers["X-Cache-Status"] == "MISS"
    assert after.json()["first_name"] == "Grace"
