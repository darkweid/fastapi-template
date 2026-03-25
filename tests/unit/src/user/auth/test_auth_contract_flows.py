from dataclasses import dataclass

from fastapi import Request
import pytest
from starlette.datastructures import URL

from src.core.errors.exceptions import UnauthorizedException
from src.core.schemas import SuccessResponse, TokenModel
from src.user.auth.dependencies import get_access_by_refresh_token, get_current_user
from src.user.auth.jwt_payload_schema import JWTPayload
from src.user.auth.schemas import (
    LoginUserModel,
    ResetPasswordModel,
    SendResetPasswordRequestModel,
)
from src.user.auth.usecases.get_access_by_refresh import (
    get_tokens_by_refresh_user_use_case,
)
from src.user.auth.usecases.login import get_login_user_use_case
from src.user.auth.usecases.reset_password_confirm import (
    get_reset_password_confirm_use_case,
)
from src.user.auth.usecases.reset_password_request import (
    get_reset_password_request_use_case,
)
from src.user.models import User
from tests.factories.token_factory import build_refresh_payload
from tests.factories.user_factory import build_user
from tests.helpers.limiter import noop_rate_limiter
from tests.helpers.overrides import DependencyOverrides
from tests.helpers.providers import ProvideValue


@dataclass
class TokenState:
    access_token: str | None = None
    refresh_token: str | None = None


@dataclass
class ResetPasswordState:
    token: str | None = None


class LoginUseCaseFake:
    def __init__(self, state: TokenState, access_token: str, refresh_token: str):
        self._state = state
        self._access_token = access_token
        self._refresh_token = refresh_token

    async def execute(self, data: LoginUserModel) -> TokenModel:
        self._state.access_token = self._access_token
        self._state.refresh_token = self._refresh_token
        return TokenModel(
            access_token=self._access_token, refresh_token=self._refresh_token
        )


class RefreshUseCaseFake:
    def __init__(self, state: TokenState, access_token: str, refresh_token: str):
        self._state = state
        self._access_token = access_token
        self._refresh_token = refresh_token

    async def execute(self, user: User, old_token_payload: JWTPayload) -> TokenModel:
        self._state.access_token = self._access_token
        self._state.refresh_token = self._refresh_token
        return TokenModel(
            access_token=self._access_token, refresh_token=self._refresh_token
        )


class RefreshTokenDependency:
    def __init__(self, state: TokenState, user: User, payload: JWTPayload):
        self._state = state
        self._user = user
        self._payload = payload

    async def __call__(self, request: Request) -> tuple[User, JWTPayload]:
        expected = self._build_expected(self._state.refresh_token)
        token = request.headers.get("Authorization")
        if token != expected:
            raise UnauthorizedException("Invalid refresh token")
        return self._user, self._payload

    @staticmethod
    def _build_expected(token: str | None) -> str:
        if not token:
            return ""
        return f"Bearer {token}"


class AccessTokenDependency:
    def __init__(self, state: TokenState, user: User):
        self._state = state
        self._user = user

    async def __call__(self, request: Request) -> User:
        expected = self._build_expected(self._state.access_token)
        token = request.headers.get("Authorization")
        if token != expected:
            raise UnauthorizedException("Invalid access token")
        return self._user

    @staticmethod
    def _build_expected(token: str | None) -> str:
        if not token:
            return ""
        return f"Bearer {token}"


class ResetPasswordRequestUseCaseFake:
    def __init__(self, state: ResetPasswordState, token: str):
        self._state = state
        self._token = token

    async def execute(
        self, data: SendResetPasswordRequestModel, request_base_url: URL
    ) -> SuccessResponse:
        self._state.token = self._token
        return SuccessResponse(success=True)


class ResetPasswordConfirmUseCaseFake:
    def __init__(self, state: ResetPasswordState):
        self._state = state

    async def execute(self, data: ResetPasswordModel) -> SuccessResponse:
        if data.token != self._state.token:
            raise ValueError("Reset token mismatch")
        return SuccessResponse(success=True)


@pytest.fixture(autouse=True)
def disable_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.core.limiter.depends.RateLimiter.__call__",
        noop_rate_limiter,
    )


@pytest.mark.asyncio
async def test_login_refresh_access_flow(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    """
    Given: a client receives initial login tokens and later uses refreshed tokens.
    When: login, refresh, and /users/me requests are executed in sequence.
    Then: each step succeeds and /users/me is authorized by the latest access token.
    """
    state = TokenState()
    user = build_user()
    payload = build_refresh_payload(str(user.id))

    login_use_case = LoginUseCaseFake(
        state=state,
        access_token="access-1",
        refresh_token="refresh-1",
    )
    refresh_use_case = RefreshUseCaseFake(
        state=state,
        access_token="access-2",
        refresh_token="refresh-2",
    )
    refresh_dependency = RefreshTokenDependency(state, user, payload)
    access_dependency = AccessTokenDependency(state, user)

    dependency_overrides.set(get_login_user_use_case, ProvideValue(login_use_case))
    dependency_overrides.set(
        get_tokens_by_refresh_user_use_case, ProvideValue(refresh_use_case)
    )
    dependency_overrides.set(get_access_by_refresh_token, refresh_dependency)
    dependency_overrides.set(get_current_user, access_dependency)

    login_response = await async_client.post(
        "/v1/users/auth/login",
        json={"email": "user@example.com", "password": "StrongPass1!"},
    )

    assert login_response.status_code == 200
    assert login_response.json() == {
        "access_token": "access-1",
        "refresh_token": "refresh-1",
    }

    refresh_response = await async_client.post(
        "/v1/users/auth/login/refresh",
        headers={"Authorization": f"Bearer {state.refresh_token}"},
    )

    assert refresh_response.status_code == 200
    assert refresh_response.json() == {
        "access_token": "access-2",
        "refresh_token": "refresh-2",
    }

    me_response = await async_client.get(
        "/v1/users/me",
        headers={"Authorization": f"Bearer {state.access_token}"},
    )

    assert me_response.status_code == 200
    assert me_response.json()["id"] == str(user.id)


@pytest.mark.asyncio
async def test_reset_password_flow(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    """
    Given: reset request use case issues a token and confirm use case validates it.
    When: reset request and reset confirm endpoints are called with matching token data.
    Then: both endpoints return success and the issued token is accepted by confirm flow.
    """
    state = ResetPasswordState()
    request_use_case = ResetPasswordRequestUseCaseFake(state, token="reset-token")
    confirm_use_case = ResetPasswordConfirmUseCaseFake(state)

    dependency_overrides.set(
        get_reset_password_request_use_case, ProvideValue(request_use_case)
    )
    dependency_overrides.set(
        get_reset_password_confirm_use_case, ProvideValue(confirm_use_case)
    )

    request_response = await async_client.post(
        "/v1/users/auth/password/reset",
        json={"email": "user@example.com"},
    )

    assert request_response.status_code == 200
    assert request_response.json() == {"success": True}
    assert state.token == "reset-token"

    confirm_response = await async_client.put(
        "/v1/users/auth/password/reset/confirm",
        json={"token": state.token, "password": "StrongPass1!"},
    )

    assert confirm_response.status_code == 200
    assert confirm_response.json() == {"success": True}
