from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.redis import lifecycle
from src.core.redis.dependencies import get_redis_client


@pytest.mark.asyncio
async def test_on_redis_startup_and_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SimpleNamespace(
        ping=AsyncMock(return_value=True),
        aclose=AsyncMock(return_value=None),
    )

    monkeypatch.setattr(
        lifecycle,
        "create_redis_client",
        lambda connection_url, decode_responses=True: client,
    )

    app = SimpleNamespace(state=SimpleNamespace())

    await lifecycle.on_redis_startup(app, "redis://example")  # type: ignore[arg-type]

    assert getattr(app.state, "redis_client") is client
    client.ping.assert_awaited_once()

    await lifecycle.on_redis_shutdown(app)  # type: ignore[arg-type]
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_redis_client_returns_from_state() -> None:
    redis_client = object()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(redis_client=redis_client))
    )

    resolved = await get_redis_client(request)  # type: ignore[arg-type]

    assert resolved is redis_client


@pytest.mark.asyncio
async def test_get_redis_client_missing_raises() -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    with pytest.raises(RuntimeError):
        await get_redis_client(request)  # type: ignore[arg-type]
