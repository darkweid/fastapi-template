from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.limiter import FastAPILimiter
from src.core.redis.cache.backend.redis_backend import RedisCacheBackend
from src.core.redis.cache.lifecycle import (
    on_redis_cache_shutdown,
    on_redis_cache_startup,
)
from src.main.config import config


@pytest.mark.asyncio
async def test_redis_cache_lifecycle_calls_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter_init = AsyncMock()
    limiter_close = AsyncMock()
    backend = RedisCacheBackend()
    backend_connect = AsyncMock()
    backend_close = AsyncMock()

    monkeypatch.setattr(FastAPILimiter, "init", limiter_init)
    monkeypatch.setattr(FastAPILimiter, "close", limiter_close)
    monkeypatch.setattr(backend, "connect", backend_connect)
    monkeypatch.setattr(backend, "close", backend_close)

    await on_redis_cache_startup()
    await on_redis_cache_shutdown()

    limiter_init.assert_awaited_once_with(config.redis.dsn)
    backend_connect.assert_awaited_once_with(config.redis.dsn)
    backend_close.assert_awaited_once()
    limiter_close.assert_awaited_once()
