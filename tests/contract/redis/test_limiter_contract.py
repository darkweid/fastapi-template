import asyncio
from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import pytest
from redis.asyncio import Redis

from src.core.limiter import FastAPILimiter
from src.core.limiter.depends import RateLimiter
from src.core.redis.degradation import RedisDegradationReporter

PREFIX = "contract-limiter"
WINDOW_MS = 60_000


@pytest.fixture
async def limiter_redis(real_redis: Redis) -> AsyncIterator[Redis]:
    """Initialize the class-level limiter on the real instance and put back what
    was there: `init` rewrites the class prefix for the rest of the session."""
    previous = (FastAPILimiter.redis, FastAPILimiter.lua_sha, FastAPILimiter.prefix)
    previous_state = (RateLimiter._fallback_windows, RateLimiter._degradation_reporter)
    await FastAPILimiter.init(real_redis, prefix=PREFIX)
    # Class-level, so another test's outage would otherwise carry into this one.
    RateLimiter._fallback_windows = {}
    RateLimiter._degradation_reporter = RedisDegradationReporter("RateLimiter")
    try:
        yield real_redis
    finally:
        FastAPILimiter.redis, FastAPILimiter.lua_sha, FastAPILimiter.prefix = previous
        RateLimiter._fallback_windows, RateLimiter._degradation_reporter = (
            previous_state
        )


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


async def test_a_sustained_out_of_memory_is_reported_once(
    limiter_redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins what no fake can: under OOM the script still runs its GET and PTTL
    and fails only at INCR or SET, so a refused call succeeds while every
    admitted one degrades. Were the refusal read as recovery, the cooldown
    would never hold."""
    capture = MagicMock()
    monkeypatch.setattr(
        "src.core.redis.degradation.sentry_sdk.capture_message", capture
    )
    limiter = RateLimiter(times=1, seconds=60)
    full = f"{PREFIX}:oom-full"
    assert await limiter._check_limit(full) == 0  # noqa: SLF001
    original = (await limiter_redis.config_get("maxmemory"))["maxmemory"]
    await limiter_redis.config_set("maxmemory", 1)
    try:
        for attempt in range(2):
            await limiter._check_limit(f"{PREFIX}:oom-fresh-{attempt}")  # noqa: SLF001
            assert await limiter._check_limit(full) > 0  # noqa: SLF001
    finally:
        await limiter_redis.config_set("maxmemory", original)

    capture.assert_called_once()
    assert "OutOfMemoryError" in capture.call_args.args[0]
