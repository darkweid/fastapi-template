from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
import pytest

from src.main import lifespan as lifespan_module
from src.main.lifespan import lifespan


@pytest.mark.asyncio
async def test_lifespan_initializes_and_shutdowns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_sentry = Mock()
    redis_startup = AsyncMock()
    redis_shutdown = AsyncMock()
    cache_startup = AsyncMock()
    cache_shutdown = AsyncMock()

    monkeypatch.setattr(lifespan_module, "init_sentry", init_sentry)
    monkeypatch.setattr(lifespan_module, "on_redis_startup", redis_startup)
    monkeypatch.setattr(lifespan_module, "on_redis_shutdown", redis_shutdown)
    monkeypatch.setattr(lifespan_module, "on_redis_cache_startup", cache_startup)
    monkeypatch.setattr(lifespan_module, "on_redis_cache_shutdown", cache_shutdown)

    app = FastAPI()
    async with lifespan(app):
        pass

    init_sentry.assert_called_once()
    redis_startup.assert_awaited_once()
    cache_startup.assert_awaited_once()
    cache_shutdown.assert_awaited_once()
    redis_shutdown.assert_awaited_once()
