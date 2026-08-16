from __future__ import annotations

from collections.abc import Generator
from unittest.mock import Mock

from fastapi import FastAPI
import pytest

from src.main import lifespan as lifespan_module
from src.main.lifespan import lifespan


@pytest.fixture
def patched_infra_lifecycle(monkeypatch: pytest.MonkeyPatch) -> Generator[list[str]]:
    """Stub every infra startup/shutdown hook lifespan calls, recording call order."""
    calls: list[str] = []

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

    monkeypatch.setattr(
        lifespan_module,
        "init_sentry",
        Mock(side_effect=lambda: calls.append("init_sentry")),
    )
    monkeypatch.setattr(lifespan_module, "on_redis_startup", redis_startup)
    monkeypatch.setattr(lifespan_module, "on_redis_shutdown", redis_shutdown)
    monkeypatch.setattr(lifespan_module, "on_limiter_startup", limiter_startup)
    monkeypatch.setattr(lifespan_module, "on_limiter_shutdown", limiter_shutdown)
    monkeypatch.setattr(lifespan_module, "on_cache_startup", cache_startup)
    monkeypatch.setattr(lifespan_module, "on_cache_shutdown", cache_shutdown)

    yield calls


@pytest.mark.asyncio
async def test_lifespan_initializes_and_shutdowns(
    patched_infra_lifecycle: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lifespan_module.config.s3, "S3_ENABLED", False)

    app = FastAPI()
    async with lifespan(app):
        pass

    assert patched_infra_lifecycle == [
        "init_sentry",
        "redis_startup",
        "limiter_startup",
        "cache_startup",
        "cache_shutdown",
        "limiter_shutdown",
        "redis_shutdown",
    ]
    assert not hasattr(app.state, "s3_adapter")


@pytest.mark.asyncio
async def test_lifespan_skips_s3_adapter_when_disabled(
    patched_infra_lifecycle: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lifespan_module.config.s3, "S3_ENABLED", False)
    build_s3_adapter = Mock()
    monkeypatch.setattr(lifespan_module, "build_s3_adapter", build_s3_adapter)

    app = FastAPI()
    async with lifespan(app):
        assert not hasattr(app.state, "s3_adapter")

    build_s3_adapter.assert_not_called()


@pytest.mark.asyncio
async def test_lifespan_builds_and_tears_down_s3_adapter_when_enabled(
    patched_infra_lifecycle: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lifespan_module.config.s3, "S3_ENABLED", True)

    class FakeS3Adapter:
        async def __aenter__(self) -> FakeS3Adapter:
            patched_infra_lifecycle.append("s3_enter")
            return self

        async def __aexit__(self, *args: object) -> None:
            patched_infra_lifecycle.append("s3_exit")

    fake_adapter = FakeS3Adapter()
    build_s3_adapter = Mock(return_value=fake_adapter)
    monkeypatch.setattr(lifespan_module, "build_s3_adapter", build_s3_adapter)

    app = FastAPI()
    async with lifespan(app):
        assert app.state.s3_adapter is fake_adapter
        assert "s3_enter" in patched_infra_lifecycle
        assert "s3_exit" not in patched_infra_lifecycle

    build_s3_adapter.assert_called_once_with(lifespan_module.config.s3)
    assert "s3_exit" in patched_infra_lifecycle
    # S3 is torn down before the cache/limiter/redis clients it does not depend on.
    assert patched_infra_lifecycle.index("s3_exit") < patched_infra_lifecycle.index(
        "cache_shutdown"
    )
