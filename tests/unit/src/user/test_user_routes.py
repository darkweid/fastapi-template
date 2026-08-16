from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.database.session import get_session
from src.core.errors.exceptions import (
    AccessForbiddenException,
    InstanceNotFoundException,
    InstanceProcessingException,
)
from src.core.schemas import SuccessResponse
from src.user.auth.dependencies import get_current_user
from src.user.auth.token_transport import TokenTransport, get_token_transport
from src.user.dependencies import get_user_service
from src.user.enums import UserRole
from src.user.schemas import UserProfileViewModel
from src.user.usecases.update_password import get_update_user_password_use_case
from src.user.usecases.update_profile import get_update_user_profile_use_case
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession
from tests.helpers.limiter import noop_rate_limiter
from tests.helpers.overrides import DependencyOverrides
from tests.helpers.providers import ProvideAsyncValue, ProvideValue


class FakeUpdatePasswordUseCase:
    def __init__(
        self,
        result: SuccessResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.execute = AsyncMock(return_value=result, side_effect=error)


class FakeUpdateProfileUseCase:
    def __init__(
        self,
        result: UserProfileViewModel | None = None,
        error: Exception | None = None,
    ) -> None:
        self.execute = AsyncMock(return_value=result, side_effect=error)


class FakeUserService:
    def __init__(self, user) -> None:
        self.get_single = AsyncMock(return_value=user)
        self.get_single_or_404 = AsyncMock()
        if user is None:
            self.get_single_or_404.side_effect = InstanceNotFoundException(
                "User not found"
            )
        else:
            self.get_single_or_404.return_value = user


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
async def test_get_user_info_by_id_returns_404_when_user_is_missing(
    async_client,
    dependency_overrides: DependencyOverrides,
    fake_session: FakeAsyncSession,
) -> None:
    admin_user = build_user(role=UserRole.ADMIN)
    missing_user_id = build_user().id
    dependency_overrides.set(get_current_user, ProvideValue(admin_user))
    dependency_overrides.set(get_user_service, ProvideValue(FakeUserService(None)))
    dependency_overrides.set(get_session, ProvideAsyncValue(fake_session))

    response = await async_client.get(f"/v1/users/{missing_user_id}")

    assert response.status_code == 404
    assert response.json() == {
        "code": "not_found",
        "message": "User not found",
    }


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
        json={"current_password": "OldPass1!", "password": "StrongPass1!"},
    )

    assert response.status_code == 200
    assert response.json() == {"success": True}


@pytest.mark.asyncio
async def test_update_user_password_expires_the_auth_cookies(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    """
    Every session is gone server-side, so leaving the browser holding a refresh
    cookie only turns the next refresh into an unexplainable logout.
    """
    user = build_user()
    dependency_overrides.set(get_current_user, ProvideValue(user))
    dependency_overrides.set(get_token_transport, ProvideValue(TokenTransport.COOKIE))
    dependency_overrides.set(
        get_update_user_password_use_case,
        ProvideValue(FakeUpdatePasswordUseCase(SuccessResponse(success=True))),
    )

    response = await async_client.patch(
        "/v1/users/me/password",
        json={"current_password": "OldPass1!", "password": "StrongPass1!"},
    )

    assert response.status_code == 200
    expired = [
        cookie
        for cookie in response.headers.get_list("set-cookie")
        if "Max-Age=0" in cookie
    ]
    assert len(expired) == 2


@pytest.mark.asyncio
async def test_update_user_password_rejects_reusing_the_current_password(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    user = build_user()
    dependency_overrides.set(get_current_user, ProvideValue(user))
    dependency_overrides.set(
        get_update_user_password_use_case,
        ProvideValue(
            FakeUpdatePasswordUseCase(
                error=InstanceProcessingException(
                    "New password must differ from the current one."
                )
            )
        ),
    )

    response = await async_client.patch(
        "/v1/users/me/password",
        json={"current_password": "StrongPass1!", "password": "StrongPass1!"},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_user_password_requires_current_password(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    user = build_user()
    use_case = FakeUpdatePasswordUseCase(SuccessResponse(success=True))
    dependency_overrides.set(get_current_user, ProvideValue(user))
    dependency_overrides.set(get_update_user_password_use_case, ProvideValue(use_case))

    response = await async_client.patch(
        "/v1/users/me/password",
        json={"password": "StrongPass1!"},
    )

    assert response.status_code == 422
    use_case.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_user_password_rejects_wrong_current_password(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    user = build_user()
    dependency_overrides.set(get_current_user, ProvideValue(user))
    dependency_overrides.set(
        get_update_user_password_use_case,
        ProvideValue(
            FakeUpdatePasswordUseCase(
                error=AccessForbiddenException("Current password is incorrect.")
            )
        ),
    )

    response = await async_client.patch(
        "/v1/users/me/password",
        json={"current_password": "WrongPass1!", "password": "StrongPass1!"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_update_user_profile_returns_404_when_user_is_missing(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    user = build_user()
    dependency_overrides.set(get_current_user, ProvideValue(user))
    dependency_overrides.set(
        get_update_user_profile_use_case,
        ProvideValue(
            FakeUpdateProfileUseCase(error=InstanceNotFoundException("User not found"))
        ),
    )

    response = await async_client.patch("/v1/users/me", json={"first_name": "Grace"})

    assert response.status_code == 404
    assert response.json() == {
        "code": "not_found",
        "message": "User not found",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"first_name": ""}, id="first_name-below-min-length"),
        pytest.param({"first_name": "x" * 51}, id="first_name-above-max-length"),
        pytest.param({"username": "ab"}, id="username-below-min-length"),
        pytest.param({"username": "x" * 51}, id="username-above-max-length"),
        pytest.param({"nickname": "Grace"}, id="unknown-field-is-rejected"),
    ],
)
async def test_update_user_profile_rejects_invalid_payloads(
    async_client,
    dependency_overrides: DependencyOverrides,
    payload: dict[str, str],
) -> None:
    user = build_user()
    dependency_overrides.set(get_current_user, ProvideValue(user))
    dependency_overrides.set(
        get_update_user_profile_use_case,
        ProvideValue(FakeUpdateProfileUseCase()),
    )

    response = await async_client.patch("/v1/users/me", json=payload)

    assert response.status_code == 422
