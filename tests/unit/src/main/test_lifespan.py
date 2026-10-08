from __future__ import annotations

from collections.abc import Generator
from unittest.mock import Mock

from fastapi import FastAPI
import pytest

from src.main import lifespan as lifespan_module
from src.main.lifespan import lifespan


class FakeRedisClient:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def aclose(self) -> None:
        self._calls.append("redis_close")


@pytest.fixture
def patched_infra_lifecycle(monkeypatch: pytest.MonkeyPatch) -> Generator[list[str]]:
    """Stub every infra startup/shutdown hook lifespan calls, recording call order."""
    calls: list[str] = []
    redis_client = FakeRedisClient(calls)

    def create_redis_client(connection_url: str) -> FakeRedisClient:
        calls.append("redis_create")
        return redis_client

    async def verify_redis_client(client: FakeRedisClient) -> None:
        calls.append("redis_verify")

    async def limiter_init(redis_client: object) -> None:
        calls.append("limiter_startup")

    async def limiter_close() -> None:
        calls.append("limiter_shutdown")

    async def cache_startup(app: FastAPI) -> None:
        calls.append("cache_startup")

    async def cache_shutdown() -> None:
        calls.append("cache_shutdown")

    async def broker_startup() -> None:
        calls.append("broker_startup")

    async def broker_shutdown() -> None:
        calls.append("broker_shutdown")

    async def http_shutdown() -> None:
        calls.append("http_shutdown")

    monkeypatch.setattr(
        lifespan_module,
        "init_sentry",
        Mock(side_effect=lambda: calls.append("init_sentry")),
    )
    monkeypatch.setattr(lifespan_module, "create_redis_client", create_redis_client)
    monkeypatch.setattr(lifespan_module, "verify_redis_client", verify_redis_client)
    monkeypatch.setattr(lifespan_module.FastAPILimiter, "init", limiter_init)
    monkeypatch.setattr(lifespan_module.FastAPILimiter, "close", limiter_close)
    monkeypatch.setattr(lifespan_module, "on_cache_startup", cache_startup)
    monkeypatch.setattr(lifespan_module, "on_cache_shutdown", cache_shutdown)
    monkeypatch.setattr(lifespan_module.broker, "is_worker_process", False)
    monkeypatch.setattr(lifespan_module.broker, "startup", broker_startup)
    monkeypatch.setattr(lifespan_module.broker, "shutdown", broker_shutdown)
    monkeypatch.setattr(lifespan_module, "close_http_clients", http_shutdown)
    monkeypatch.setattr(lifespan_module.config.s3, "S3_ENABLED", False)

    yield calls


@pytest.mark.asyncio
async def test_lifespan_initializes_and_shutdowns(
    patched_infra_lifecycle: list[str],
) -> None:
    app = FastAPI()
    async with lifespan(app):
        assert isinstance(app.state.redis_client, FakeRedisClient)

    assert patched_infra_lifecycle == [
        "init_sentry",
        "redis_create",
        "redis_verify",
        "limiter_startup",
        "cache_startup",
        "broker_startup",
        "http_shutdown",
        "broker_shutdown",
        "cache_shutdown",
        "limiter_shutdown",
        "redis_close",
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


@pytest.mark.asyncio
async def test_every_resource_closes_in_reverse_order_when_the_app_stops_on_an_error(
    patched_infra_lifecycle: list[str],
) -> None:
    """An abnormal shutdown is when leaked sockets and pools are least noticed."""
    with pytest.raises(RuntimeError, match="server crashed"):
        async with lifespan(FastAPI()):
            raise RuntimeError("server crashed")

    started = patched_infra_lifecycle.index("broker_startup")
    assert patched_infra_lifecycle[started + 1 :] == [
        "http_shutdown",
        "broker_shutdown",
        "cache_shutdown",
        "limiter_shutdown",
        "redis_close",
    ]


@pytest.mark.asyncio
async def test_a_failed_broker_startup_still_closes_what_was_opened(
    patched_infra_lifecycle: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RedisStreamBroker.startup opens connection pools before the step that can
    fail, so shutdown, which only disconnects them, runs on a half-started broker."""

    async def broker_startup() -> None:
        patched_infra_lifecycle.append("broker_startup")
        raise RuntimeError("XGROUP CREATE failed")

    monkeypatch.setattr(lifespan_module.broker, "startup", broker_startup)

    with pytest.raises(RuntimeError, match="XGROUP CREATE failed"):
        async with lifespan(FastAPI()):
            pytest.fail("the app must not start")

    started = patched_infra_lifecycle.index("broker_startup")
    assert patched_infra_lifecycle[started + 1 :] == [
        "broker_shutdown",
        "cache_shutdown",
        "limiter_shutdown",
        "redis_close",
    ]
    assert "http_shutdown" not in patched_infra_lifecycle


@pytest.mark.asyncio
async def test_a_failed_redis_ping_closes_the_client(
    patched_infra_lifecycle: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pool created before the ping must not outlive the failed startup."""

    async def verify_redis_client(client: FakeRedisClient) -> None:
        patched_infra_lifecycle.append("redis_verify")
        raise RuntimeError("Redis ping failed during startup")

    monkeypatch.setattr(lifespan_module, "verify_redis_client", verify_redis_client)
    app = FastAPI()

    with pytest.raises(RuntimeError, match="Redis ping failed"):
        async with lifespan(app):
            pytest.fail("the app must not start")

    assert patched_infra_lifecycle == [
        "init_sentry",
        "redis_create",
        "redis_verify",
        "redis_close",
    ]
    assert not hasattr(app.state, "redis_client")


@pytest.mark.asyncio
async def test_worker_process_leaves_the_broker_alone(
    patched_infra_lifecycle: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker CLI starts and stops the broker itself; a second pair from the
    lifespan would shut it down under the running worker."""
    monkeypatch.setattr(lifespan_module.broker, "is_worker_process", True)

    async with lifespan(FastAPI()):
        pass

    assert "broker_startup" not in patched_infra_lifecycle
    assert "broker_shutdown" not in patched_infra_lifecycle
