from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import Response
import pytest
import redis.exceptions as redis_exc

from src.core.limiter import FastAPILimiter
from src.core.limiter.depends import RateLimiter
from tests.fakes.redis import InMemoryRedis
from tests.helpers.requests import build_request


async def sample_endpoint() -> None:
    return None


class ErrorRedis:
    async def evalsha(self, *_args: object, **_kwargs: object) -> int:
        raise redis_exc.ConnectionError("down")


@pytest.fixture
def limiter_state() -> tuple[object | None, str | None]:
    prev_redis = FastAPILimiter.redis
    prev_sha = FastAPILimiter.lua_sha
    yield prev_redis, prev_sha
    FastAPILimiter.redis = prev_redis
    FastAPILimiter.lua_sha = prev_sha


def test_rate_limiter_invalid_window_raises() -> None:
    with pytest.raises(ValueError, match="Rate limiter window must be greater than 0"):
        RateLimiter(times=1)


@pytest.mark.asyncio
async def test_rate_limiter_raises_when_not_initialized(
    limiter_state: tuple[object | None, str | None],
) -> None:
    FastAPILimiter.redis = None
    FastAPILimiter.lua_sha = None
    limiter = RateLimiter(times=1, seconds=1)
    request = build_request(path="/test", endpoint=sample_endpoint)
    response = Response()

    with pytest.raises(RuntimeError, match="FastAPILimiter must be initialized"):
        await limiter(request, response)


@pytest.mark.asyncio
async def test_rate_limiter_allows_request_when_under_limit(
    limiter_state: tuple[object | None, str | None],
    fake_redis: InMemoryRedis,
) -> None:
    FastAPILimiter.redis = fake_redis
    FastAPILimiter.lua_sha = await fake_redis.script_load(FastAPILimiter.lua_script)
    callback = AsyncMock()
    limiter = RateLimiter(times=1, seconds=1, callback=callback)
    request = build_request(path="/test", endpoint=sample_endpoint)
    response = Response()

    await limiter(request, response)

    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limiter_calls_callback_on_limit(
    limiter_state: tuple[object | None, str | None],
    fake_redis: InMemoryRedis,
) -> None:
    FastAPILimiter.redis = fake_redis
    FastAPILimiter.lua_sha = await fake_redis.script_load(FastAPILimiter.lua_script)
    callback = AsyncMock()
    limiter = RateLimiter(times=1, seconds=1, callback=callback)
    request = build_request(path="/test", endpoint=sample_endpoint)
    response = Response()

    rate_key = await FastAPILimiter.identifier(request)
    key = f"{FastAPILimiter.prefix}:{rate_key}:{sample_endpoint.__name__}"
    fake_redis.set_evalsha_result(key, 1000)

    await limiter(request, response)

    callback.assert_awaited_once_with(request, response, 1000)


@pytest.mark.asyncio
async def test_rate_limiter_loads_script_when_missing(
    limiter_state: tuple[object | None, str | None],
    fake_redis: InMemoryRedis,
) -> None:
    FastAPILimiter.redis = fake_redis
    FastAPILimiter.lua_sha = "missing"
    callback = AsyncMock()
    limiter = RateLimiter(times=1, seconds=1, callback=callback)
    request = build_request(path="/test", endpoint=sample_endpoint)
    response = Response()

    await limiter(request, response)

    assert FastAPILimiter.lua_sha in fake_redis._scripts
    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limiter_skips_on_redis_error(
    limiter_state: tuple[object | None, str | None],
) -> None:
    FastAPILimiter.redis = ErrorRedis()
    FastAPILimiter.lua_sha = "sha"
    callback = AsyncMock()
    limiter = RateLimiter(times=1, seconds=1, callback=callback)
    request = build_request(path="/test", endpoint=sample_endpoint)
    response = Response()

    await limiter(request, response)

    callback.assert_not_awaited()
