from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.database.session import get_session
from src.core.schemas import SuccessResponse
from src.user.auth.dependencies import get_current_user
from src.user.dependencies import get_user_service
from src.user.enums import UserRole
from src.user.usecases.update_password import get_update_user_password_use_case
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession
from tests.helpers.limiter import noop_rate_limiter
from tests.helpers.overrides import DependencyOverrides
from tests.helpers.providers import ProvideAsyncValue, ProvideValue


class FakeUpdatePasswordUseCase:
    def __init__(self, result: SuccessResponse) -> None:
        self.execute = AsyncMock(return_value=result)


class FakeUserService:
    def __init__(self, user) -> None:
        self.get_single = AsyncMock(return_value=user)


@pytest.fixture(autouse=True)
def disable_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.core.limiter.depends.RateLimiter.__call__",
        noop_rate_limiter,
    )


@pytest.mark.asyncio
async def test_get_user_profile(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    user = build_user()
    dependency_overrides.set(get_current_user, ProvideValue(user))

    response = await async_client.get("/v1/users/me")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == str(user.id)
    assert payload["email"] == user.email


@pytest.mark.asyncio
async def test_get_user_info_by_id(
    async_client,
    dependency_overrides: DependencyOverrides,
    fake_session: FakeAsyncSession,
) -> None:
    admin_user = build_user(role=UserRole.ADMIN)
    target_user = build_user()
    dependency_overrides.set(get_current_user, ProvideValue(admin_user))
    dependency_overrides.set(
        get_user_service, ProvideValue(FakeUserService(target_user))
    )
    dependency_overrides.set(get_session, ProvideAsyncValue(fake_session))

    response = await async_client.get(f"/v1/users/{target_user.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == str(target_user.id)
    assert payload["username"] == target_user.username


@pytest.mark.asyncio
async def test_update_user_password(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    user = build_user()
    dependency_overrides.set(get_current_user, ProvideValue(user))
    dependency_overrides.set(
        get_update_user_password_use_case,
        ProvideValue(FakeUpdatePasswordUseCase(SuccessResponse(success=True))),
    )

    response = await async_client.patch(
        "/v1/users/me/password",
        json={"password": "StrongPass1!"},
    )

    assert response.status_code == 200
    assert response.json() == {"success": True}
