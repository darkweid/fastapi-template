"""
Pins the order of the refresh route's gates against the *real* rate limiter.

Every other refresh test replaces `RateLimiter.__call__` with a no-op, which also
skips the limiter's identifier - and the identifier is precisely what must not run
before the CSRF check. `get_user_id_from_token` reads the refresh cookie and calls
`verify_jti` on it, so a forged cross-site request that reached it would consume one
of the victim's five refresh slots per fifteen minutes (five such requests lock the
legitimate client out) and could trigger reuse detection, all before the 403.
"""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

from fastapi import FastAPI
import pytest
import pytest_asyncio

from src.core.database.session import get_session
from src.core.limiter import FastAPILimiter
from src.core.redis.dependencies import get_redis_client
from src.core.schemas import TokenModel
from src.main.config import get_settings
from src.user.auth.cookies import (
    CSRF_HEADER_NAME,
    REFRESH_COOKIE_NAME,
    REFRESH_COOKIE_PATH,
)
from src.user.auth.csrf import build_csrf_token
from src.user.auth.usecases.get_access_by_refresh import (
    get_tokens_by_refresh_user_use_case,
)
from src.user.dependencies import get_user_repository
from src.user.models import User
from tests.factories.token_factory import build_refresh_token
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession
from tests.fakes.redis import InMemoryRedis
from tests.helpers.overrides import DependencyOverrides
from tests.helpers.providers import ProvideAsyncValue, ProvideValue


class FakeUseCase:
    def __init__(self, result: TokenModel) -> None:
        self.execute = AsyncMock(return_value=result)


class FakeUserRepository:
    def __init__(self, user: User | None) -> None:
        self.get_single = AsyncMock(return_value=user)


@pytest_asyncio.fixture
async def live_limiter(
    app: FastAPI, fake_redis: InMemoryRedis
) -> AsyncGenerator[InMemoryRedis]:
    """
    Point the real limiter at the fake redis, and put it back afterwards.

    `app.state.redis_client` is set too: `get_user_id_from_token` calls
    `get_redis_client(request)` directly rather than through DI, so a dependency
    override would not reach it.
    """
    previous_redis = FastAPILimiter.redis
    previous_sha = FastAPILimiter.lua_sha
    previous_state_client = getattr(app.state, "redis_client", None)
    FastAPILimiter.redis = fake_redis
    FastAPILimiter.lua_sha = await fake_redis.script_load(FastAPILimiter.lua_script)
    app.state.redis_client = fake_redis
    yield fake_redis
    FastAPILimiter.redis = previous_redis
    FastAPILimiter.lua_sha = previous_sha
    app.state.redis_client = previous_state_client


def _limiter_keys(fake_redis: InMemoryRedis) -> list[str]:
    prefix = f"{FastAPILimiter.prefix}:"
    return [key for key in fake_redis.evalsha_keys if key.startswith(prefix)]


@pytest.mark.asyncio
async def test_csrf_failure_does_not_consume_the_user_rate_limit(
    async_client,
    dependency_overrides: DependencyOverrides,
    live_limiter: InMemoryRedis,
    fake_session: FakeAsyncSession,
) -> None:
    user = build_user()
    refresh_token = await build_refresh_token({"sub": str(user.id)}, live_limiter)

    dependency_overrides.set(get_redis_client, ProvideValue(live_limiter))
    dependency_overrides.set(get_session, ProvideAsyncValue(fake_session))
    dependency_overrides.set(
        get_user_repository, ProvideValue(FakeUserRepository(user))
    )
    dependency_overrides.set(
        get_tokens_by_refresh_user_use_case,
        ProvideValue(FakeUseCase(TokenModel(access_token="a", refresh_token="r"))),
    )

    async_client.cookies.set(
        REFRESH_COOKIE_NAME, refresh_token, path=REFRESH_COOKIE_PATH
    )
    live_limiter.evalsha_keys.clear()

    response = await async_client.post("/v1/users/auth/login/refresh")

    assert response.status_code == 403
    assert response.json()["message"] == "CSRF validation failed"

    keys = _limiter_keys(live_limiter)
    # The IP-scoped limiter is in front of the CSRF gate on purpose: it still bounds
    # an unauthenticated flood. Asserting it ran proves the limiter really is live
    # here, so the assertion below is about ordering and not about a disabled limiter.
    assert keys, "The rate limiter never ran - this test would pass vacuously"
    assert not any(
        str(user.id) in key for key in keys
    ), f"The user-scoped limiter ran before the CSRF gate: {keys}"


@pytest.mark.asyncio
async def test_valid_csrf_still_consumes_the_user_rate_limit(
    async_client,
    dependency_overrides: DependencyOverrides,
    live_limiter: InMemoryRedis,
    fake_session: FakeAsyncSession,
) -> None:
    """The gate reorder must not disable the per-user limit for genuine traffic."""
    user = build_user()
    refresh_token = await build_refresh_token({"sub": str(user.id)}, live_limiter)
    tokens = TokenModel(access_token="access-2", refresh_token="refresh-2")

    dependency_overrides.set(get_redis_client, ProvideValue(live_limiter))
    dependency_overrides.set(get_session, ProvideAsyncValue(fake_session))
    dependency_overrides.set(
        get_user_repository, ProvideValue(FakeUserRepository(user))
    )
    dependency_overrides.set(
        get_tokens_by_refresh_user_use_case, ProvideValue(FakeUseCase(tokens))
    )

    async_client.cookies.set(
        REFRESH_COOKIE_NAME, refresh_token, path=REFRESH_COOKIE_PATH
    )
    live_limiter.evalsha_keys.clear()

    response = await async_client.post(
        "/v1/users/auth/login/refresh",
        headers={
            CSRF_HEADER_NAME: build_csrf_token(
                refresh_token, get_settings().cookie.CSRF_SECRET_KEY
            )
        },
    )

    assert response.status_code == 200
    keys = _limiter_keys(live_limiter)
    assert any(
        str(user.id) in key for key in keys
    ), f"The user-scoped limiter never ran for a legitimate request: {keys}"
