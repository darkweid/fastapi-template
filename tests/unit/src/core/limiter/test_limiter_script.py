from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.limiter import FastAPILimiter


class FakeRedis:
    def __init__(self) -> None:
        self.script_load = AsyncMock(return_value="sha")
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def reset_limiter_state() -> None:
    prev_redis = FastAPILimiter.redis
    prev_sha = FastAPILimiter.lua_sha
    yield
    FastAPILimiter.redis = prev_redis
    FastAPILimiter.lua_sha = prev_sha


@pytest.mark.asyncio
async def test_fastapi_limiter_init_sets_state() -> None:
    redis = FakeRedis()

    await FastAPILimiter.init(redis_client=redis, prefix="test")

    assert FastAPILimiter.redis is redis
    assert FastAPILimiter.lua_sha == "sha"
    assert FastAPILimiter.is_initialized() is True
    redis.script_load.assert_awaited_once()
