from __future__ import annotations

from unittest.mock import Mock

from fastapi import FastAPI
import pytest

from src.main import lifespan as lifespan_module
from src.main.lifespan import lifespan


@pytest.mark.asyncio
async def test_lifespan_initializes_and_shutdowns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    init_sentry = Mock(side_effect=lambda: calls.append("init_sentry"))

    async def redis_startup(app: FastAPI, dsn: str) -> None:
        calls.append("redis_startup")

    async def redis_shutdown(app: FastAPI) -> None:
        calls.append("redis_shutdown")

    async def limiter_startup(dsn: str) -> None:
        calls.append("limiter_startup")

    async def limiter_shutdown() -> None:
        calls.append("limiter_shutdown")

    async def cache_startup(app: FastAPI) -> None:
        calls.append("cache_startup")

    async def cache_shutdown() -> None:
        calls.append("cache_shutdown")

    monkeypatch.setattr(lifespan_module, "init_sentry", init_sentry)
    monkeypatch.setattr(lifespan_module, "on_redis_startup", redis_startup)
    monkeypatch.setattr(lifespan_module, "on_redis_shutdown", redis_shutdown)
    monkeypatch.setattr(lifespan_module, "on_limiter_startup", limiter_startup)
    monkeypatch.setattr(lifespan_module, "on_limiter_shutdown", limiter_shutdown)
    monkeypatch.setattr(lifespan_module, "on_cache_startup", cache_startup)
    monkeypatch.setattr(lifespan_module, "on_cache_shutdown", cache_shutdown)

    app = FastAPI()
    async with lifespan(app):
        pass

    assert calls == [
        "init_sentry",
        "redis_startup",
        "limiter_startup",
        "cache_startup",
        "cache_shutdown",
        "limiter_shutdown",
        "redis_shutdown",
    ]
