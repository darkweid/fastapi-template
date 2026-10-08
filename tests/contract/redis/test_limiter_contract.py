import asyncio
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from src.core.limiter import FastAPILimiter
from src.core.limiter.depends import RateLimiter

PREFIX = "contract-limiter"
WINDOW_MS = 60_000


@pytest.fixture
async def limiter_redis(real_redis: Redis) -> AsyncIterator[Redis]:
    """Initialize the class-level limiter on the real instance and put back what
    was there: `init` rewrites the class prefix for the rest of the session."""
    previous = (FastAPILimiter.redis, FastAPILimiter.lua_sha, FastAPILimiter.prefix)
    await FastAPILimiter.init(real_redis, prefix=PREFIX)
    try:
        yield real_redis
    finally:
        FastAPILimiter.redis, FastAPILimiter.lua_sha, FastAPILimiter.prefix = previous


async def test_the_window_admits_the_limit_then_answers_the_wait(
    limiter_redis: Redis,
) -> None:
    """The third call inside a two-per-minute window is the one that must be
    refused, and the answer is the time left, which becomes Retry-After. The
    counter is read back from Redis because the limiter falls back to memory on
    any Redis error and would pass this test without Redis."""
    limiter = RateLimiter(times=2, seconds=60)
    key = f"{PREFIX}:window"

    assert await limiter._check_limit(key) == 0  # noqa: SLF001
    assert await limiter._check_limit(key) == 0  # noqa: SLF001
    remaining_ms = await limiter._check_limit(key)  # noqa: SLF001

    assert 0 < remaining_ms <= WINDOW_MS
    assert await limiter_redis.get(key) == "2"


async def test_a_new_window_starts_counting_again(limiter_redis: Redis) -> None:
    """Once the window key is gone the caller has a fresh budget; asserting the
    key's lifetime stands in for waiting a minute."""
    limiter = RateLimiter(times=1, seconds=60)
    key = f"{PREFIX}:reset"
    assert await limiter._check_limit(key) == 0  # noqa: SLF001
    assert await limiter._check_limit(key) > 0  # noqa: SLF001

    await limiter_redis.delete(key)

    assert await limiter._check_limit(key) == 0  # noqa: SLF001
    assert await limiter_redis.get(key) == "1"
    assert 0 < await limiter_redis.pttl(key) <= WINDOW_MS


async def test_a_flushed_script_cache_is_reloaded(limiter_redis: Redis) -> None:
    """A Redis restart or SCRIPT FLUSH drops the cached script; the limiter must
    reload it, not fall back to memory for the rest of the process."""
    limiter = RateLimiter(times=5, seconds=60)
    key = f"{PREFIX}:noscript"
    await limiter_redis.script_flush()

    assert await limiter._check_limit(key) == 0  # noqa: SLF001
    assert await limiter_redis.get(key) == "1"


async def test_concurrent_calls_never_exceed_the_limit(limiter_redis: Redis) -> None:
    """A burst arriving at once is exactly what a limit is for."""
    limiter = RateLimiter(times=3, seconds=60)
    key = f"{PREFIX}:burst"

    results = await asyncio.gather(
        *(limiter._check_limit(key) for _ in range(10))  # noqa: SLF001
    )

    assert sum(result == 0 for result in results) == 3
    assert await limiter_redis.get(key) == "3"
