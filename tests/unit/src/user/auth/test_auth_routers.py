from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.schemas import SuccessResponse, TokenModel
from src.user.auth.dependencies import get_access_by_refresh_token
from src.user.auth.jwt_payload_schema import JWTPayload
from src.user.auth.usecases.get_access_by_refresh import (
    get_tokens_by_refresh_user_use_case,
)
from src.user.auth.usecases.login import get_login_user_use_case
from src.user.auth.usecases.register import get_register_use_case
from src.user.auth.usecases.resend_verification import get_send_verification_use_case
from src.user.auth.usecases.reset_password_confirm import (
    get_reset_password_confirm_use_case,
)
from src.user.auth.usecases.reset_password_request import (
    get_reset_password_request_use_case,
)
from src.user.auth.usecases.verify_email import get_verify_email_use_case
from src.user.schemas import UserProfileViewModel
from tests.factories.token_factory import build_refresh_payload
from tests.factories.user_factory import build_user
from tests.helpers.limiter import noop_rate_limiter
from tests.helpers.overrides import DependencyOverrides
from tests.helpers.providers import ProvideValue


class FakeUseCase:
    def __init__(self, result) -> None:
        self.execute = AsyncMock(return_value=result)


@pytest.fixture(autouse=True)
def disable_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.core.limiter.depends.RateLimiter.__call__",
        noop_rate_limiter,
    )


@pytest.mark.asyncio
async def test_register_endpoint(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    user = build_user()
    result = UserProfileViewModel.model_validate(user)
    dependency_overrides.set(get_register_use_case, ProvideValue(FakeUseCase(result)))

    response = await async_client.post(
        "/v1/users/auth/register",
        json={
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "username": "john_doe",
            "phone_number": "+1234567890",
            "password": "StrongPass1!",
        },
    )

    assert response.status_code == 201
    assert response.json()["email"] == user.email


@pytest.mark.asyncio
async def test_login_endpoint(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    tokens = TokenModel(access_token="a", refresh_token="r")
    dependency_overrides.set(get_login_user_use_case, ProvideValue(FakeUseCase(tokens)))

    response = await async_client.post(
        "/v1/users/auth/login",
        json={"email": "user@example.com", "password": "StrongPass1!"},
    )

    assert response.status_code == 200
    assert response.json() == {"access_token": "a", "refresh_token": "r"}


@pytest.mark.asyncio
async def test_refresh_endpoint(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    user = build_user()
    payload: JWTPayload = build_refresh_payload(str(user.id))
    dependency_overrides.set(get_access_by_refresh_token, ProvideValue((user, payload)))
    tokens = TokenModel(access_token="a", refresh_token="r")
    dependency_overrides.set(
        get_tokens_by_refresh_user_use_case, ProvideValue(FakeUseCase(tokens))
    )

    response = await async_client.post("/v1/users/auth/login/refresh")

    assert response.status_code == 200
    assert response.json() == {"access_token": "a", "refresh_token": "r"}


@pytest.mark.asyncio
async def test_send_verification_email_endpoint(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    dependency_overrides.set(
        get_send_verification_use_case,
        ProvideValue(FakeUseCase(SuccessResponse(success=True))),
    )

    response = await async_client.post(
        "/v1/users/auth/verification-email",
        json={"email": "user@example.com"},
    )

    assert response.status_code == 200
    assert response.json() == {"success": True}


@pytest.mark.asyncio
async def test_verify_email_endpoint(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    dependency_overrides.set(
        get_verify_email_use_case,
        ProvideValue(FakeUseCase(SuccessResponse(success=True))),
    )

    response = await async_client.get("/v1/users/auth/verify", params={"token": "t"})

    assert response.status_code == 200
    assert response.json() == {"success": True}


@pytest.mark.asyncio
async def test_reset_password_request_endpoint(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    dependency_overrides.set(
        get_reset_password_request_use_case,
        ProvideValue(FakeUseCase(SuccessResponse(success=True))),
    )

    response = await async_client.post(
        "/v1/users/auth/password/reset",
        json={"email": "user@example.com"},
    )

    assert response.status_code == 200
    assert response.json() == {"success": True}


@pytest.mark.asyncio
async def test_reset_password_confirm_endpoint(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    dependency_overrides.set(
        get_reset_password_confirm_use_case,
        ProvideValue(FakeUseCase(SuccessResponse(success=True))),
    )

    response = await async_client.put(
        "/v1/users/auth/password/reset/confirm",
        json={"token": "t", "password": "StrongPass1!"},
    )

    assert response.status_code == 200
    assert response.json() == {"success": True}
