from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock

from fastapi import Response
import pytest

from src.core.limiter import FastAPILimiter, default_identifier
from src.core.limiter.depends import RateLimiter
from src.core.redis.degradation import RedisDegradationReporter
from tests.fakes.redis import InMemoryRedis
from tests.helpers.requests import build_request


async def sample_endpoint() -> None:
    return None


@pytest.fixture
def limiter_state() -> Iterator[None]:
    prev_redis = FastAPILimiter.redis
    prev_sha = FastAPILimiter.lua_sha
    prev_windows = RateLimiter._fallback_windows.copy()
    prev_reporter = RateLimiter._degradation_reporter
    RateLimiter._fallback_windows = {}
    RateLimiter._degradation_reporter = RedisDegradationReporter("RateLimiter")
    yield
    FastAPILimiter.redis = prev_redis
    FastAPILimiter.lua_sha = prev_sha
    RateLimiter._fallback_windows = prev_windows
    RateLimiter._degradation_reporter = prev_reporter


@pytest.mark.asyncio
async def test_default_identifier_ignores_forwarded_for_header() -> None:
    plain = build_request(path="/test")
    forged = build_request(
        path="/test",
        headers={"X-Forwarded-For": "9.9.9.9, 8.8.8.8"},
    )

    assert await default_identifier(plain) == await default_identifier(forged)


@pytest.mark.asyncio
async def test_default_identifier_uses_peer_address() -> None:
    request = build_request(path="/test")

    assert await default_identifier(request) == "127.0.0.1:/test"


@pytest.mark.asyncio
async def test_unique_forwarded_for_per_request_shares_one_limit_window(
    limiter_state: None,
    fake_redis: InMemoryRedis,
) -> None:
    """A fresh XFF per request must not hand out a fresh limiter bucket."""
    FastAPILimiter.redis = fake_redis
    FastAPILimiter.lua_sha = await fake_redis.script_load(FastAPILimiter.lua_script)
    limiter = RateLimiter(times=2, seconds=60, callback=AsyncMock())
    response = Response()

    for index in range(3):
        request = build_request(
            path="/test",
            endpoint=sample_endpoint,
            headers={"X-Forwarded-For": f"203.0.113.{index}"},
        )
        await limiter(request, response)

    endpoint_name = f"{sample_endpoint.__module__}.{sample_endpoint.__qualname__}"
    assert fake_redis.evalsha_keys == [f"limiter:127.0.0.1:/test:{endpoint_name}"] * 3
