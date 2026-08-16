from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.errors.exceptions import (
    InstanceNotFoundException,
    InstanceProcessingException,
)
from src.core.schemas import SuccessResponse
from src.user.auth.dependencies import (
    AuthenticatedUser,
    authenticate_access_token,
    get_authenticated_user,
    get_current_user,
)
from src.user.auth.errors import InvalidCredentialsError
from src.user.auth.token_transport import TokenTransport, get_token_transport
from src.user.dependencies import get_user_service
from src.user.enums import UserRole
from src.user.schemas import UserProfileViewModel
from src.user.usecases.update_password import get_update_user_password_use_case
from src.user.usecases.update_profile import get_update_user_profile_use_case
from tests.factories.user_factory import build_user
from tests.helpers.limiter import noop_rate_limiter
from tests.helpers.overrides import DependencyOverrides
from tests.helpers.providers import ProvideValue


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
    dependency_overrides.set(get_authenticated_user, ProvideValue(user))

    response = await async_client.get("/v1/users/me")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == str(user.id)
    assert payload["email"] == user.email
    assert payload["is_active"] is True


@pytest.mark.asyncio
async def test_get_user_profile_exposes_blocked_state(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    """The /me admission opt-out exists so a client can inspect its account
    state during session bootstrap; the blocked flag must be visible there."""
    user = build_user(is_active=False)
    dependency_overrides.set(get_authenticated_user, ProvideValue(user))

    response = await async_client.get("/v1/users/me")

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_active"] is False
    assert payload["is_verified"] is True


@pytest.mark.asyncio
async def test_get_user_info_by_id(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    admin_user = build_user(role=UserRole.ADMIN)
    target_user = build_user()
    dependency_overrides.set(get_current_user, ProvideValue(admin_user))
    dependency_overrides.set(
        get_user_service, ProvideValue(FakeUserService(target_user))
    )

    response = await async_client.get(f"/v1/users/{target_user.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == str(target_user.id)
    assert payload["username"] == target_user.username


@pytest.mark.asyncio
async def test_get_user_info_by_id_denies_without_view_users_permission(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    # When a VIEWER tries to access another user's ID without VIEW_USERS permission,
    # the BOLA guard returns 404 to prevent enumeration: the response is indistinguishable
    # from a genuinely missing user, so the endpoint cannot be used to discover valid IDs.
    viewer_user = build_user(role=UserRole.VIEWER)
    target_user = build_user()
    dependency_overrides.set(get_current_user, ProvideValue(viewer_user))
    dependency_overrides.set(
        get_user_service, ProvideValue(FakeUserService(target_user))
    )

    response = await async_client.get(f"/v1/users/{target_user.id}")

    assert response.status_code == 404
    assert response.json() == {
        "code": "not_found",
        "message": "User not found.",
    }


@pytest.mark.asyncio
async def test_get_user_info_by_id_returns_404_when_user_is_missing(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    admin_user = build_user(role=UserRole.ADMIN)
    missing_user_id = build_user().id
    dependency_overrides.set(get_current_user, ProvideValue(admin_user))
    dependency_overrides.set(get_user_service, ProvideValue(FakeUserService(None)))

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
                error=InvalidCredentialsError("Current password is incorrect.")
            )
        ),
    )

    response = await async_client.patch(
        "/v1/users/me/password",
        json={"current_password": "WrongPass1!", "password": "StrongPass1!"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "code": "invalid_credentials",
        "message": "Current password is incorrect.",
    }


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
        pytest.param({"first_name": "a" * 31}, id="first_name-above-max-length"),
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


@pytest.mark.asyncio
async def test_blocked_user_cannot_update_profile(
    async_client, dependency_overrides: DependencyOverrides
) -> None:
    blocked = build_user(is_active=False)
    dependency_overrides.set(
        authenticate_access_token,
        ProvideValue(AuthenticatedUser(user=blocked, session_id="sid")),
    )

    response = await async_client.patch("/v1/users/me", json={"username": "new-name"})

    assert response.status_code == 403
    assert response.json() == {"code": "user_blocked", "message": "User is blocked"}


@pytest.mark.asyncio
async def test_blocked_user_cannot_change_password(
    async_client, dependency_overrides: DependencyOverrides
) -> None:
    blocked = build_user(is_active=False)
    dependency_overrides.set(
        authenticate_access_token,
        ProvideValue(AuthenticatedUser(user=blocked, session_id="sid")),
    )

    response = await async_client.patch(
        "/v1/users/me/password",
        json={"current_password": "Current-1", "password": "Password-1"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "user_blocked"


@pytest.mark.asyncio
async def test_unverified_user_still_reads_own_profile(
    async_client, dependency_overrides: DependencyOverrides
) -> None:
    unverified = build_user(is_verified=False)
    dependency_overrides.set(
        authenticate_access_token,
        ProvideValue(AuthenticatedUser(user=unverified, session_id="sid")),
    )

    response = await async_client.get("/v1/users/me")

    assert response.status_code == 200
    assert response.json()["id"] == str(unverified.id)
